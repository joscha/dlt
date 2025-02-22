"""Tests translation of `dlt` credentials into `object_store` Rust crate credentials."""

from typing import Any, Dict

import os
import json  # noqa: I251
import pytest
from deltalake import DeltaTable
from deltalake.exceptions import TableNotFoundError

import dlt
from dlt.common.configuration import resolve_configuration
from dlt.common.configuration.specs import (
    AnyAzureCredentials,
    AzureServicePrincipalCredentialsWithoutDefaults,
    AzureCredentialsWithoutDefaults,
    AwsCredentials,
    AwsCredentialsWithoutDefaults,
    GcpCredentials,
    GcpServiceAccountCredentialsWithoutDefaults,
    GcpOAuthCredentialsWithoutDefaults,
)
from dlt.common.utils import custom_environ
from dlt.common.configuration.resolve import resolve_configuration
from dlt.common.configuration.specs.gcp_credentials import GcpDefaultCredentials
from dlt.common.configuration.specs.exceptions import ObjectStoreRsCredentialsException

from tests.load.utils import (
    AZ_BUCKET,
    AWS_BUCKET,
    GCS_BUCKET,
    R2_BUCKET_CONFIG,
    ALL_FILESYSTEM_DRIVERS,
)


pytestmark = pytest.mark.essential

if all(driver not in ALL_FILESYSTEM_DRIVERS for driver in ("az", "s3", "gs", "r2")):
    pytest.skip(
        "Requires at least one of `az`, `s3`, `gs`, `r2` in `ALL_FILESYSTEM_DRIVERS`.",
        allow_module_level=True,
    )


@pytest.fixture
def fs_creds() -> Dict[str, Any]:
    creds: Dict[str, Any] = dlt.secrets.get("destination.filesystem.credentials")
    if creds is None:
        pytest.skip(
            msg="`destination.filesystem.credentials` must be configured for these tests.",
        )
    return creds


def can_connect(bucket_url: str, object_store_rs_credentials: Dict[str, str]) -> bool:
    """Returns True if client can connect to object store, False otherwise.

    Uses `deltatable` library as Python interface to `object_store` Rust crate.
    """
    try:
        DeltaTable(
            bucket_url,
            storage_options=object_store_rs_credentials,
        )
    except TableNotFoundError:
        # this error implies the connection was successful
        # there is no Delta table at `bucket_url`
        return True
    return False


@pytest.mark.parametrize(
    "driver", [driver for driver in ALL_FILESYSTEM_DRIVERS if driver in ("az")]
)
def test_azure_object_store_rs_credentials(driver: str, fs_creds: Dict[str, Any]) -> None:
    creds: AnyAzureCredentials

    creds = AzureServicePrincipalCredentialsWithoutDefaults(
        **dlt.secrets.get("destination.fsazureprincipal.credentials")
    )
    assert can_connect(AZ_BUCKET, creds.to_object_store_rs_credentials())

    # without SAS token
    creds = AzureCredentialsWithoutDefaults(
        azure_storage_account_name=fs_creds["azure_storage_account_name"],
        azure_storage_account_key=fs_creds["azure_storage_account_key"],
    )
    assert creds.azure_storage_sas_token is None
    assert can_connect(AZ_BUCKET, creds.to_object_store_rs_credentials())

    # with SAS token
    creds = resolve_configuration(creds)
    assert creds.azure_storage_sas_token is not None
    assert can_connect(AZ_BUCKET, creds.to_object_store_rs_credentials())


@pytest.mark.parametrize(
    "driver", [driver for driver in ALL_FILESYSTEM_DRIVERS if driver in ("s3", "r2")]
)
def test_aws_object_store_rs_credentials(driver: str, fs_creds: Dict[str, Any]) -> None:
    creds: AwsCredentialsWithoutDefaults

    if driver == "r2":
        fs_creds = R2_BUCKET_CONFIG["credentials"]  # type: ignore[assignment]

    # AwsCredentialsWithoutDefaults: no user-provided session token
    creds = AwsCredentialsWithoutDefaults(
        aws_access_key_id=fs_creds["aws_access_key_id"],
        aws_secret_access_key=fs_creds["aws_secret_access_key"],
        region_name=fs_creds.get("region_name"),
        endpoint_url=fs_creds.get("endpoint_url"),
    )
    assert creds.aws_session_token is None
    object_store_rs_creds = creds.to_object_store_rs_credentials()
    assert "aws_session_token" not in object_store_rs_creds  # no auto-generated token
    assert can_connect(AWS_BUCKET, object_store_rs_creds)

    # AwsCredentials: no user-provided session token
    creds = AwsCredentials(
        aws_access_key_id=fs_creds["aws_access_key_id"],
        aws_secret_access_key=fs_creds["aws_secret_access_key"],
        region_name=fs_creds.get("region_name"),
        endpoint_url=fs_creds.get("endpoint_url"),
    )
    assert creds.aws_session_token is None
    object_store_rs_creds = creds.to_object_store_rs_credentials()
    assert "aws_session_token" not in object_store_rs_creds  # no auto-generated token
    assert can_connect(AWS_BUCKET, object_store_rs_creds)

    # exception should be raised if both `endpoint_url` and `region_name` are
    # not provided
    with pytest.raises(ObjectStoreRsCredentialsException):
        AwsCredentials(
            aws_access_key_id=fs_creds["aws_access_key_id"],
            aws_secret_access_key=fs_creds["aws_secret_access_key"],
        ).to_object_store_rs_credentials()

    if "endpoint_url" in object_store_rs_creds:
        # TODO: make sure this case is tested on GitHub CI, e.g. by adding
        # a local MinIO bucket to the set of tested buckets
        if object_store_rs_creds["endpoint_url"].startswith("http://"):
            assert object_store_rs_creds["aws_allow_http"] == "true"

        # remainder of tests use session tokens
        # we don't run them on S3 compatible storage because session tokens
        # may not be available
        return

    # AwsCredentials: user-provided session token
    # use previous credentials to create session token for new credentials
    assert isinstance(creds, AwsCredentials)
    sess_creds = creds.to_session_credentials()
    creds = AwsCredentials(
        aws_access_key_id=sess_creds["aws_access_key_id"],
        aws_secret_access_key=sess_creds["aws_secret_access_key"],
        aws_session_token=sess_creds["aws_session_token"],
        region_name=fs_creds["region_name"],
    )
    assert creds.aws_session_token is not None
    object_store_rs_creds = creds.to_object_store_rs_credentials()
    assert object_store_rs_creds["aws_session_token"] is not None
    assert can_connect(AWS_BUCKET, object_store_rs_creds)

    # AwsCredentialsWithoutDefaults: user-provided session token
    creds = AwsCredentialsWithoutDefaults(
        aws_access_key_id=sess_creds["aws_access_key_id"],
        aws_secret_access_key=sess_creds["aws_secret_access_key"],
        aws_session_token=sess_creds["aws_session_token"],
        region_name=fs_creds["region_name"],
    )
    assert creds.aws_session_token is not None
    object_store_rs_creds = creds.to_object_store_rs_credentials()
    assert object_store_rs_creds["aws_session_token"] is not None
    assert can_connect(AWS_BUCKET, object_store_rs_creds)


@pytest.mark.parametrize(
    "driver", [driver for driver in ALL_FILESYSTEM_DRIVERS if driver in ("gs")]
)
def test_gcp_object_store_rs_credentials(driver, fs_creds: Dict[str, Any]) -> None:
    creds: GcpCredentials

    # GcpServiceAccountCredentialsWithoutDefaults
    creds = GcpServiceAccountCredentialsWithoutDefaults(
        project_id=fs_creds["project_id"],
        private_key=fs_creds["private_key"],
        # private_key_id must be configured in order for data lake to work
        private_key_id=fs_creds["private_key_id"],
        client_email=fs_creds["client_email"],
    )
    assert can_connect(GCS_BUCKET, creds.to_object_store_rs_credentials())

    # GcpDefaultCredentials

    # reset failed default credentials timeout so we resolve below
    GcpDefaultCredentials._LAST_FAILED_DEFAULT = 0

    # write service account key to JSON file
    service_json = json.loads(creds.to_object_store_rs_credentials()["service_account_key"])
    path = "_secrets/service.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(service_json, f)

    with custom_environ({"GOOGLE_APPLICATION_CREDENTIALS": path}):
        creds = GcpDefaultCredentials()
        resolve_configuration(creds)
        can_connect(GCS_BUCKET, creds.to_object_store_rs_credentials())

    # GcpOAuthCredentialsWithoutDefaults is currently not supported
    with pytest.raises(NotImplementedError):
        GcpOAuthCredentialsWithoutDefaults().to_object_store_rs_credentials()
