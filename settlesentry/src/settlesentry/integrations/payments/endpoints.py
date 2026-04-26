from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping
from urllib.parse import urljoin

from settlesentry.core import get_logger, settings

logger = get_logger("EndpointRegistry")


class EndpointName(StrEnum):
    LOOKUP_ACCOUNT = "lookup_account"
    PROCESS_PAYMENT = "process_payment"


@dataclass(frozen=True)
class EndpointSpec:
    name: EndpointName
    method: str
    path: str
    timeout_seconds: int
    retryable: bool = True


ENDPOINT_SPECS: Mapping[EndpointName, EndpointSpec] = {
    EndpointName.LOOKUP_ACCOUNT: EndpointSpec(
        name=EndpointName.LOOKUP_ACCOUNT,
        method="POST",
        path="/api/lookup-account",
        timeout_seconds=settings.api.timeout_seconds,
        retryable=True,
    ),
    EndpointName.PROCESS_PAYMENT: EndpointSpec(
        name=EndpointName.PROCESS_PAYMENT,
        method="POST",
        path="/api/process-payment",
        timeout_seconds=settings.api.timeout_seconds,
        retryable=False,
    ),
}


class EndpointRegistry:
    def __init__(
        self,
        base_url: str,
        endpoints: Mapping[EndpointName, EndpointSpec] = ENDPOINT_SPECS,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self._endpoints = endpoints

    def get(self, name: EndpointName) -> EndpointSpec:
        try:
            return self._endpoints[name]
        except KeyError as exc:
            logger.debug(f"Attempted to access unknown endpoint: {name}")
            raise ValueError(f"Unknown endpoint: {name}") from exc

    def url_for(self, name: EndpointName) -> str:
        endpoint = self.get(name)
        return urljoin(self.base_url, endpoint.path.lstrip("/"))

    def method_for(self, name: EndpointName) -> str:
        return self.get(name).method


endpoints = EndpointRegistry(base_url=settings.api.base_url)

if __name__ == "__main__":
    for endpoint_name in EndpointName:
        url = endpoints.url_for(endpoint_name)
        method = endpoints.method_for(endpoint_name)
        logger.debug(f"Endpoint: {endpoint_name} | Method: {method} | URL: {url}")
