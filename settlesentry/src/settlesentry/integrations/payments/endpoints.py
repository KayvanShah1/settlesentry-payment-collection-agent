from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Mapping
from urllib.parse import urljoin

from settlesentry.core import get_logger, settings

logger = get_logger("EndpointRegistry")


class EndpointName(StrEnum):
    LOOKUP_ACCOUNT = auto()
    PROCESS_PAYMENT = auto()


@dataclass(frozen=True)
class EndpointSpec:
    name: EndpointName
    path: str
    timeout_seconds: int
    method: str = "POST"
    retryable: bool = True


ENDPOINT_SPECS: Mapping[EndpointName, EndpointSpec] = {
    EndpointName.LOOKUP_ACCOUNT: EndpointSpec(
        name=EndpointName.LOOKUP_ACCOUNT,
        path="/api/lookup-account",
        timeout_seconds=settings.api.timeout_seconds,
        retryable=True,
    ),
    EndpointName.PROCESS_PAYMENT: EndpointSpec(
        name=EndpointName.PROCESS_PAYMENT,
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
        return self._endpoints[name]

    def url_for(self, name: EndpointName) -> str:
        endpoint = self.get(name)
        return urljoin(self.base_url, endpoint.path.lstrip("/"))

    def method_for(self, name: EndpointName) -> str:
        return self.get(name).method


endpoint_registry = EndpointRegistry(base_url=settings.api.base_url)

if __name__ == "__main__":
    for endpoint_name in EndpointName:
        url = endpoint_registry.url_for(endpoint_name)
        method = endpoint_registry.method_for(endpoint_name)
        logger.debug(f"Endpoint: {endpoint_name} | Method: {method}\nURL: {url}")
