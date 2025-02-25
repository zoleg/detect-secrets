import inspect
import sys
from functools import lru_cache
from typing import Any
from typing import Iterable
from typing import List
from typing import Optional
from typing import TypeVar

from ..custom_types import NamedIO
from ..util.importlib import import_types_from_package
from .base import BaseTransformer
from .exceptions import ParsingError


Transformer = TypeVar('Transformer', bound=BaseTransformer)


def get_transformed_file(
    file: NamedIO,
    use_eager_transformers: bool = False,
) -> Optional[List[str]]:
    for transformer in get_transformers():
        if not transformer.should_parse_file(file.name):
            continue

        if use_eager_transformers != transformer.is_eager:
            continue

        try:
            return transformer.parse_file(file)
        except ParsingError:
            pass
        finally:
            file.seek(0)

    return None


@lru_cache(maxsize=1)
def get_transformers() -> Iterable[Transformer]:
    return [
        item()
        for item in import_types_from_package(
            sys.modules[__name__],
            filter=lambda x: not _is_valid_transformer(x),
        )
    ]


def _is_valid_transformer(attribute: Any) -> bool:
    return (
        inspect.isclass(attribute)
        and issubclass(attribute, BaseTransformer)
        and attribute.__name__ != 'BaseTransformer'
    )
