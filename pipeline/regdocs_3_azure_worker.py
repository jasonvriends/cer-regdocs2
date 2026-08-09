#!/usr/bin/env python3
"""Internal Azure Stage 3 worker entry point with SDK retries disabled.

The large implementation lives in ``regdocs_3_azure_worker_impl.py``. This
wrapper exists so the public Azure path can enforce a cost-safety invariant at
the Azure SDK transport layer without coupling that policy to the reusable
implementation module.
"""

from __future__ import annotations

import regdocs_3_azure_worker_impl as impl


def make_no_retry_client(args):
    """Construct Content Understanding with Azure SDK transport retries disabled."""
    if not args.endpoint:
        raise RuntimeError(
            "Azure Content Understanding endpoint is required. "
            "Pass --endpoint or set CONTENTUNDERSTANDING_ENDPOINT."
        )

    if args.key:
        credential = impl.AzureKeyCredential(args.key)
    else:
        credential = impl.DefaultAzureCredential()

    client = impl.ContentUnderstandingClient(
        endpoint=args.endpoint,
        credential=credential,
        api_version=args.api_version,
        polling_interval=args.polling_interval,
        retry_total=0,
        retry_connect=0,
        retry_read=0,
        retry_status=0,
    )
    return client, credential


# main() resolves make_client from its module globals at runtime, so replacing
# it here applies the no-retry transport policy to every Azure client it creates.
impl.make_client = make_no_retry_client


if __name__ == "__main__":
    raise SystemExit(impl.main())
