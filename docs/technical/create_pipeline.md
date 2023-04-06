# Create Pipeline
marks features that are:

⛔ not implemented, hard to add

☮️ not implemented, easy to add


## Example from `dlt` module docstring
It is possible to create "intuitive" pipeline just by providing a list of objects to `dlt.run` methods No decorators and secret files, configurations are necessary.

```python
import dlt
import requests

dlt.run(
  requests.get("https://api.chess.com/pub/player/magnuscarlsen/games/2022/11").json()["games"],
  destination="duckdb",
  table_name="magnus_games"
)
```

Run your pipeline script
`$ python magnus_games.py`

See and query your data with autogenerated Streamlit app
`$ dlt pipeline dlt_magnus_games show`

## Source extractor function the preferred way
General guidelines:
1. the source extractor is a function decorated with `@dlt.source`. that function **yields** or **returns** a list of resources.
2. resources are generator functions that always **yield** data (enforced by exception which I hope is user friendly). Access to external endpoints, databases etc. should happen from that generator function. Generator functions may be decorated with `@dlt.resource` to provide alternative names, write disposition etc.
3. resource generator functions can be OFC parametrized and resources may be created dynamically
4. the resource generator function may yield **anything that is json serializable**. we prefer to yield _dict_ or list of dicts.
> yielding lists is much more efficient in terms of processing!
5. like any other iterator, the @dlt.source and @dlt.resource **can be iterated and thus extracted and loaded only once**, see example below.

**Remarks:**

1. the **@dlt.resource** let's you define the table schema hints: `name`, `write_disposition`, `columns`
2. the **@dlt.source** let's you define global schema props: `name` (which is also source name), `schema` which is Schema object if explicit schema is provided `nesting` to set nesting level etc.
3. decorators can also be used as functions ie in case of dlt.resource and `lazy_function` (see examples)

```python
endpoints = ["songs", "playlist", "albums"]
# return list of resourced
return [dlt.resource(lazy_function(endpoint, name=endpoint) for endpoint in endpoints)]

```

### Extracting data
Source function is not meant to extract the data, but in many cases getting some metadata ie. to generate dynamic resources (like in case of google sheets example) is unavoidable. The source function's body is evaluated **outside** the pipeline `run` (if `dlt.source` is a generator, it is immediately consumed).

Actual extraction of the data should happen inside the `dlt.resource` which is lazily executed inside the `dlt` pipeline.

> both a `dlt` source and resource are regular Python iterators and can be passed to any python function that accepts them ie to `list`. `dlt` will evaluate such iterators, also parallel and async ones and provide mock state to it.

## Multiple resources and resource selection when loading
The source extraction function may contain multiple resources. The resources can be defined as multiple resource functions or created dynamically ie. with parametrized generators.
The user of the pipeline can check what resources are available and select the resources to load.


**each resource has a a separate resource function**
```python
import requests
import dlt

@dlt.source
def hubspot(...):

    @dlt.resource(write_disposition="replace")
    def users():
        # calls to API happens here
        ...
        yield users

    @dlt.resource(write_disposition="append")
    def transactions():
        ...
        yield transactions

    # return a list of resources
    return users, transactions

# load all resources
taktile_data(1).run(destination=bigquery)
# load only decisions
taktile_data(1).with_resources("decisions").run(....)

# alternative form:
source = taktile_data(1)
# select only decisions to be loaded
source.resources.select("decisions")
# see what is selected
print(source.selected_resources)
# same as this
print(source.resources.selected)
```

Except being accessible via `source.resources` dictionary, **every resource is available as an attribute of the source**. For the example above
```python
print(list(source.decisions))  # will iterate decisions resource
source.logs.selected = False  # deselect resource
```

## Resources may be created dynamically
Here we implement a single parametrized function that **yields** data and we call it repeatedly. Mind that the function body won't be executed immediately, only later when generator is consumed in extract stage.

```python

@dlt.source
def spotify():

    endpoints = ["songs", "playlists", "albums"]

    def get_resource(endpoint):
        # here we yield the whole response
        yield requests.get(url + "/" + endpoint).json()

    # here we yield resources because this produces cleaner code
    for endpoint in endpoints:
        # calling get_resource creates generator, the actual code of the function will be executed in extractor
        yield dlt.resource(get_resource(endpoint), name=endpoint)

```

## Unbound (parametrized) resources
Imagine the situation in which you have a resource for which you want (or require) user to pass some options ie. the number of records returned.

> try it, it is ⚡ powerful

1. In all examples above you do that via the source and returned resources are not parametrized.
OR
2. You can return a **parametrized (unbound)** resources from the source.

```python

@dlt.source
def chess(chess_api_url):

    # let people choose player title, the default is grand master
    @dlt.resource
    def players(title_filter="GM", max_results=10):
        yield

    # ❗ return the players without the calling
    return players

s = chess("url")
# let's parametrize the resource to select masters. you simply call `bind` method on the resource to bind it
# if you do not bind it, the default values are used
s.players.bind("M", max_results=1000)
# load the masters
s.run()

```

## A standalone @resource
A general purpose resource (ie. jsonl reader, generic sql query reader etc.) that you want to add to any of your sources or multiple instances of it to your pipelines?
Yeah definitely possible. Just replace `@source` with `@resource` decorator.

```python
@dlt.resource(name="logs", write_disposition="append")
def taktile_data(initial_log_id, taktile_api_key=dlt.secret.value):

    # yes, this will also work but data will be obtained immediately when taktile_data() is called.
    resp = requests.get(
        "https://taktile.com/api/v2/logs?from_log_id=%i" % initial_log_id,
        headers={"Authorization": taktile_api_key})
    resp.raise_for_status()
    for item in resp.json()["result"]:
        yield item

# this will load the resource into default schema. see `general_usage.md)
dlt.run(source=taktile_data(1), destination=bigquery)

```
How standalone resource works:
1. It can be used like a source that contains only one resource (ie. single endpoint)
2. The main difference is that when extracted it will join the default schema in the pipeline (or explicitly passed schema)
3. It can be called from a `@source` function and then it becomes a resource of that source and joins the source schema

## `dlt` state availability

The state is a python dictionary-like object that is available within the `@dlt.source` and `@dlt.resource` decorated functions and may be read and written to.
The data within the state is loaded into destination together with any other extracted data and made automatically available to the source/resource extractor functions when they are run next time.
When using the state:
* Any JSON-serializable values can be written and the read from the state.
* The state available in the `dlt source` is read only and any changes will be discarded. Still it may be used to initialize the resources.
* The state available in the `dlt resource` is writable and written values will be available only once

### State sharing and isolation across sources

1. Each source and resources **in the same Python module** (no matter if they are standalone, inner or created dynamically) share the same state dictionary and is separated from other sources
2. Source accepts `section` argument which creates a separate state for that resource (and separate configuration as well). All sources with the same `section` share the state.
2. All the standalone resources and generators that do not belong to any source share the same state when being extracted (they are extracted withing ad-hoc created source)

## Stream resources: dispatching data to several tables from single resources
What about resource like rasa tracker or singer tap that send a stream of events that should be routed to different tables? we have an answer (actually two):
1. in many cases the table name is based on the data item content (ie. you dispatch events of given type to different tables by event type). We can pass a function that takes the data item as input and returns table name.
```python
# send item to a table with name item["type"]
@dlt.resource(table_name=lambda i: i['type'])
def repo_events() -> Iterator[TDataItems]:
    yield item
```

2. You can mark the yielded data with a table name (`dlt.mark.with_table_name`). This gives you full control on the name of the table

see [here](docs/examples/sources/rasa/rasa.py) and [here](docs/examples/sources/singer_tap.py).

## Source / resource config sections and arguments injection
You should read [secrets_and_config](secrets_and_config.md) now to understand how configs and credentials are passed to the decorated functions and how the users of them can configure their projects.

Also look at the following [test](/tests/extract/test_decorators.py) : `test_source_sections`

## Example sources and resources

### With inner resource function
Resource functions can be placed inside the source extractor function. That lets them get access to source function input arguments and all the computations within the source function via so called closure.

```python
import requests
import dlt

# the `dlt.source` tell the library that the decorated function is a source
# it will use function name `taktile_data` to name the source and the generated schema by default
# in general `@source` should **return** a list of resources or list of generators (function that yield data)
# @source may also **yield** resources or generators - if yielding is more convenient
# if @source returns or yields data - this will generate exception with a proper explanation. dlt user can always load the data directly without any decorators like in the previous example!
@dlt.source
def taktile_data(initial_log_id, taktile_api_key=dlt.secret.value):

    # the `dlt.resource` tells the `dlt.source` that the function defines a resource
    # will use function name `logs` as resource/table name by default
    # the function should **yield** the data items one by one or **yield** a list.
    # here the decorator is optional: there are no parameters to `dlt.resource`
    @dlt.resource
    def logs():
        resp = requests.get(
            "https://taktile.com/api/v2/logs?from_log_id=%i" % initial_log_id,
            headers={"Authorization": taktile_api_key})
        resp.raise_for_status()
        # option 1: yield the whole list
        yield resp.json()["result"]
        # or -> this is useful if you deal with a stream of data and for that you need an API that supports that, for example you could yield lists containing paginated results
        for item in resp.json()["result"]:
            yield item

    # as mentioned we return a resource or a list of resources
    return logs
    # this will also work
    # return logs()
```

### With outer generator yielding data, and @resource created dynamically
```python

def taktile_logs_data(initial_log_id, taktile_api_key=dlt.secret.value)
    yield data


@dlt.source
def taktile_data(initial_log_id, taktile_api_key):
    # pass the arguments and convert to resource
    return dlt.resource(taktile_logs_data(initial_log_id, taktile_api_key), name="logs", write_disposition="append")
```

### A source with resources defined elsewhere
Example of the above
```python
from taktile.resources import logs

@dlt.source
def taktile_data(initial_log_id, taktile_api_key=dlt.secret.value):
    return logs(initial_log_id, taktile_api_key)
```

## Advanced Topics

### Transformers ⚡
This happens all the time:
1. We have an endpoint that returns a list of users and then we must get each profile with a separate call.
2. The situation above is getting even more complicated when we need that list in two places in our source ie. we want to get the profiles but also a list of transactions per user.

Ideally we would obtain the list only once and then call and yield from the profiles and transactions endpoint in parallel so the extraction time is minimized.

Here's example how to do that: [run resources and transformers in parallel threads](/docs/examples/chess/chess.py) and test named `test_evolve_schema`

More on transformers:
1. you can have unbound (parametrized) transformers as well
2. you can use pipe '|' operator to pipe data from resources to transformers instead of binding them statically with `data_from`.
> see our [singer tap](/docs/examples/singer_tap_jsonl_example.py) example where we pipe a stream of document from `jsonl` into `raw_singer_tap` which is a standalone, unbound ⚡ transformer.
3. If transformer yields just one element you can `return` it instead. This allows you to apply the `retry` and `defer` (parallel execution) decorators directly to it.

#### Transformer example

Here we have a list of huge documents and we want to load into several tables.

```python
@dlt.source
def spotify():

    # deselect by default, we do not want to load the huge doc
    @dlt.resource(selected=False)
    def get_huge_doc():
        return requests.get(...)

    # make songs and playlists to be dependent on get_huge_doc
    @dlt.transformer(data_from=get_huge_doc)
    def songs(huge_doc):
        yield huge_doc["songs"]

    @dlt.transformer(data_from=get_huge_doc)
    def playlists(huge_doc):
        yield huge_doc["playlists"]

    # as you can see the get_huge_doc is not even returned, nevertheless it will be evaluated (only once)
    # the huge doc will not be extracted and loaded
    return songs, playlists
    # we could also use the pipe operator, intead of providing_data from
    # return get_huge_doc | songs, get_huge_doc | playlists
```

## Data item transformations

You can attach any number of transformations to your resource that are evaluated on item per item basis. The available transformation types:
* map - transform the data item
* filter - filter the data item
* yield map - a map that returns iterator (so single row may generate many rows)

You can add and insert transformations on the `DltResource` object (ie. decorated function)
* resource.add_map
* resource.add_filter
* resource.add_yield_map

> Transformations always deal with single items even if you return lists.

You can add transformations to a resource (also within a source) **after it is created**. This allows to customize existing pipelines. The transformations may
be distributed with the pipeline or written ad hoc in pipeline script.
```python
# anonymize creates nice deterministic hash for any hashable data type (not implemented yet:)
from dlt.helpers import anonymize

# example transformation provided by the user
def anonymize_user(user_data):
    user_data["user_id"] = anonymize(user_data["user_id"])
    user_data["user_email"] = anonymize(user_data["user_email"])
    return user_data

@dlt.source
def pipedrive(...):
    ...

    @dlt.resource(write_disposition="replace")
    def users():
        ...
        users = requests.get(...)
        ...
        yield users

    return users, deals, customers
```

in pipeline script:
1. we want to remove user with id == "me"
2. we want to anonymize user data
3. we want to pivot `user_props` into KV table

```python
from pipedrive import pipedrive, anonymize_user

source = pipedrive()
# access resource in the source by name and add filter and map transformation
source.users.add_filter(lambda user: user["user_id"] != "me").add_map(anonymize_user)
# now we want to yield user props to separate table. we define our own generator function
def pivot_props(user):
  # keep user
  yield user
  # yield user props to user_props table
  yield from [
    dlt.mark.with_table_name({"user_id": user["user_id"], "name": k, "value": v}, "user_props") for k, v in user["props"]
    ]

source.user.add_yield_map(pivot_props)
pipeline.run(source)
```

We provide a library of various concrete transformations:

* ☮️ a recursive versions of the map, filter and flat map which can be applied to any nesting level of the data item (the standard transformations work on recursion level 0). Possible applications
  - ☮️ recursive rename of dict keys
  - ☮️ converting all values to strings
  - etc.

## Some CS Theory

### The power of decorators

With decorators dlt can inspect and modify the code being decorated.
1. it knows what are the sources and resources without running them
2. it knows input arguments so it knows the config values and secret values (see `secrets_and_config`). with those we can generate deployments automatically
3. it can inject config and secret values automatically
4. it wraps the functions into objects that provide additional functionalities
- sources and resources are iterators so you can write
```python
items = list(source())

for item in source()["logs"]:
    ...
```
- you can select which resources to load with `source().select(*names)`
- you can add mappings and filters to resources

### The power of yielding: The preferred way to write resources

The Python function that yields is not a function but magical object that `dlt` can control:
1. it is not executed when you call it! the call just creates a generator (see below). in the example above `taktile_data(1)` will not execute the code inside, it will just return an object composed of function code and input parameters. dlt has control over the object and can execute the code later. this is called `lazy execution`
2. i can control when and how much of the code is executed. the function that yields typically looks like that

```python
def lazy_function(endpoint_name):
    # INIT - this will be executed only once when DLT wants!
    get_configuration()
    from_item = dlt.current.state.get("last_item", 0)
    l = get_item_list_from_api(api_key, endpoint_name)

    # ITERATOR - this will be executed many times also when DLT wants more data!
    for item in l:
        yield requests.get(url, api_key, "%s?id=%s" % (endpoint_name, item["id"])).json()
    # CLEANUP
    # this will be executed only once after the last item was yielded!
    dlt.current.state["last_item"] = item["id"]
```

3. dlt will execute this generator in extractor. the whole execution is atomic (including writing to state). if anything fails with exception the whole extract function fails.
4. the execution can be parallelized by using a decorator or a simple modifier function ie:
```python
for item in l:
    yield deferred(requests.get(url, api_key, "%s?id=%s" % (endpoint_name, item["id"])).json())
```