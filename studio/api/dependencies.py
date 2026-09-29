from typing import Annotated

from fastapi import Depends, Request

from studio.presentation_service import PresentationService


def application(request: Request) -> PresentationService:
    return request.app.state.presentation_service


ServiceDep = Annotated[PresentationService, Depends(application)]
