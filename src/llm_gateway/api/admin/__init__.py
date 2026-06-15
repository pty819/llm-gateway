from fastapi import APIRouter, Depends

from llm_gateway.api.deps import admin_dep
from llm_gateway.api.admin import access, identity, observability, policy, routing


router = APIRouter(prefix="/admin", dependencies=[Depends(admin_dep)])
router.include_router(identity.router)
router.include_router(routing.router)
router.include_router(access.router)
router.include_router(observability.router)
router.include_router(policy.router)
