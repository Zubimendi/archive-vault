import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_full_api_workflow(client: AsyncClient):
    """
    End-to-end integration test exercising all 11 API folders from the Postman collection.
    """
    # 01 - Health
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

    # 02 - Tenant Onboarding
    res = await client.post("/api/v1/tenants", json={
        "name": "FinTech Global Enterprise",
        "regime_profile": "GDPR",
        "kek_ref": "arn:aws:kms:eu-central-1:123456789012:key/mrk-archivevault-tenant-kek"
    })
    assert res.status_code == 201
    tenant_id = res.json()["id"]

    headers = {"X-Tenant-ID": tenant_id}

    # 03 - Data Map Registry
    valid_map = {
        "version": 1,
        "document": {
            "stores": [
                {
                    "id": "app-postgres",
                    "connector": "postgres",
                    "datasets": [
                        {
                            "name": "users",
                            "subject_key": "id",
                            "action": "ANONYMIZE",
                            "anonymize": {
                                "email": "NULLIFY",
                                "full_name": "NULLIFY",
                                "phone": "NULLIFY"
                            }
                        },
                        {
                            "name": "support_messages",
                            "subject_key": "author_id",
                            "action": "DELETE"
                        }
                    ]
                }
            ]
        }
    }
    res = await client.post("/api/v1/data-maps", json=valid_map, headers=headers)
    assert res.status_code == 201
    dm_version_id = res.json()["id"]

    # Publish Data Map
    res = await client.post(f"/api/v1/data-maps/{dm_version_id}/publish", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "ACTIVE"

    # Negative Data Map validation (reject HASH)
    bad_map = {
        "version": 99,
        "document": {
            "stores": [
                {
                    "id": "bad-store",
                    "connector": "postgres",
                    "datasets": [
                        {
                            "name": "leads",
                            "subject_key": "id",
                            "action": "ANONYMIZE",
                            "anonymize": {"email": "SHA256_HASH"}
                        }
                    ]
                }
            ]
        }
    }
    res = await client.post("/api/v1/data-maps", json=bad_map, headers=headers)
    assert res.status_code in (400, 422)

    # 04 - Subject Registration
    res = await client.post("/api/v1/subjects", json={
        "identifier_type": "EMAIL",
        "identifier_value": "jane.doe@enterprise.io",
        "pepper_context": "production-user"
    }, headers=headers)
    assert res.status_code == 201
    subject_id = res.json()["id"]

    # Get subject status: verify zero raw identifier in output
    res = await client.get(f"/api/v1/subjects/{subject_id}", headers=headers)
    assert res.status_code == 200
    assert "jane.doe@enterprise.io" not in res.text

    # 05 - Erasure Request Intake & Planning
    res = await client.post("/api/v1/erasure-requests", json={
        "subject_id": subject_id,
        "trigger": "SUBJECT_REQUEST",
        "regime": "GDPR",
        "quarantine_days": 3
    }, headers=headers)
    assert res.status_code in (200, 201, 202)
    request_id = res.json()["id"]

    # Deduplication negative check
    res = await client.post("/api/v1/erasure-requests", json={
        "subject_id": subject_id,
        "trigger": "SUBJECT_REQUEST",
        "regime": "GDPR"
    }, headers=headers)
    assert res.status_code == 409

    # Inspect request details & steps
    res = await client.get(f"/api/v1/erasure-requests/{request_id}", headers=headers)
    assert res.status_code == 200
    assert "cutoff_at" in res.json()
    assert "steps" in res.json()

    # 06 - Saga Lifecycle & Pivot
    res = await client.post(f"/api/v1/erasure-requests/{request_id}/pivot", json={
        "bypass_quarantine_window": True,
        "reason": "Force test pivot"
    }, headers=headers)
    assert res.status_code == 200
    assert res.json()["state"] == "PURGING"

    # Post-pivot cancel negative check
    res = await client.post(f"/api/v1/erasure-requests/{request_id}/cancel", json={
        "cancellation_reason": "Late cancel attempt"
    }, headers=headers)
    assert res.status_code == 409

    # Execute saga steps until verified
    for _ in range(5):
        s_res = await client.post(f"/api/v1/erasure-requests/{request_id}/execute-step", headers=headers)
        assert s_res.status_code == 200
        if s_res.json().get("remaining_pending", 0) == 0:
            break

    # 07 - Verification & Certificate
    res = await client.post(f"/api/v1/erasure-requests/{request_id}/verify", headers=headers)
    assert res.status_code in (200, 202)

    res = await client.get(f"/api/v1/erasure-requests/{request_id}/certificate", headers=headers)
    assert res.status_code == 200
    assert "@" not in res.text
    assert "jane" not in res.text.lower()

    # Verify Ledger Hash Chain
    res = await client.get("/api/v1/ledger/verify", headers=headers)
    assert res.status_code == 200
    assert res.json()["valid"] is True

    # 08 - Legal Holds
    res = await client.post("/api/v1/holds", json={
        "subject_id": subject_id,
        "reason": "Subpoena FTC",
        "placed_by": "counsel@company.com"
    }, headers=headers)
    assert res.status_code == 201
    hold_id = res.json()["id"]

    res = await client.post(f"/api/v1/holds/{hold_id}/release", json={
        "released_by": "counsel@company.com",
        "release_reason": "Dismissed"
    }, headers=headers)
    assert res.status_code == 200

    # 09 - Suppression check
    res = await client.post("/api/v1/suppression/check", json={
        "identifier": "jane.doe@enterprise.io"
    }, headers=headers)
    assert res.status_code == 200

    # 10 - Coverage scan
    res = await client.post("/api/v1/coverage/scan", json={
        "store_id": "app-postgres"
    }, headers=headers)
    assert res.status_code == 200

    # 11 - Deadline Sweep
    res = await client.post("/api/v1/deadlines/sweep", headers=headers)
    assert res.status_code == 200
