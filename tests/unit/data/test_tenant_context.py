"""Tenant context lifecycle tests."""

def test_tenant_scope_is_nested_and_restores_parent_context() -> None:
    from packages.common.tenant_context import current_tenant_id, tenant_scope

    assert current_tenant_id() is None

    with tenant_scope("tenant-a"):
        assert current_tenant_id() == "tenant-a"
        with tenant_scope("tenant-b"):
            assert current_tenant_id() == "tenant-b"
        assert current_tenant_id() == "tenant-a"

    assert current_tenant_id() is None
