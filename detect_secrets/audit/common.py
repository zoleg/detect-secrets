import json
from contextlib import contextmanager
from functools import lru_cache
from typing import Callable
from typing import cast
from typing import Iterator
from typing import List
from typing import Optional

from . import io
from ..core import baseline
from ..core import plugins
from ..core.potential_secret import PotentialSecret
from ..core.secrets_collection import SecretsCollection
from ..custom_types import NamedIO
from ..exceptions import InvalidBaselineError
from ..exceptions import NoLineNumberError
from ..exceptions import SecretNotFoundOnSpecifiedLineError
from ..transformers import get_transformed_file
from ..util.inject import call_function_with_arguments
from detect_secrets.util.code_snippet import get_code_snippet


def get_baseline_from_file(filename: str) -> SecretsCollection:
    """
    :raises: InvalidBaselineError
    """
    try:
        # TODO: Should we upgrade this?
        return baseline.load(baseline.load_from_file(filename), filename)
    except (IOError, json.decoder.JSONDecodeError):
        io.print_error('Not a valid baseline file!')
        raise InvalidBaselineError
    except KeyError:
        io.print_error('Not a valid baseline file!')
        raise InvalidBaselineError


@lru_cache(maxsize=1)
def open_file(filename: str) -> 'LineGetter':
    return LineGetter(filename)


def get_raw_secret_from_file(
    secret: PotentialSecret,
    line_getter_factory: Callable[[str], 'LineGetter'] = open_file,
) -> Optional[str]:
    """
    We're analyzing the contents straight from the baseline, and therefore, we don't know
    the secret value (by design). However, we have line numbers, filenames, and how we detected
    it was a secret in the first place, so we can reverse-engineer it.

    :raises: SecretNotFoundOnSpecifiedLineError
    :raises: NoLineNumberError
    """
    if not secret.line_number:
        raise NoLineNumberError

    for item in get_raw_secrets_from_file(secret, line_getter_factory):
        return item.secret_value

    raise SecretNotFoundOnSpecifiedLineError(secret.line_number)


def get_raw_secrets_from_file(
    secret: PotentialSecret,
    line_getter_factory: Callable[[str], 'LineGetter'] = open_file,
) -> List[PotentialSecret]:
    """
    We're analyzing the contents straight from the baseline, and therefore, we don't know
    the secret value (by design). However, we have secret hashes, filenames, and how we detected
    it was a secret in the first place, so we can reverse-engineer it. This method searches all
    the occurrences of one secret in one file using one plugin.

    :raises: SecretNotFoundOnSpecifiedLineError
    :raises: NoLineNumberError
    """
    plugin = plugins.initialize.from_secret_type(secret.type)
    line_getter = line_getter_factory(secret.filename)
    is_first_time_opening_file = not line_getter.has_cached_lines
    all_secrets = []
    while True:
        if secret.line_number:
            try:
                lines_to_scan = [line_getter.lines[secret.line_number - 1]]
                line_numbers = [secret.line_number - 1]
            except IndexError:
                raise SecretNotFoundOnSpecifiedLineError(secret.line_number)
        else:
            lines_to_scan = line_getter.lines
            line_numbers = list(range(len(lines_to_scan)))

        for line_number, line in zip(line_numbers, lines_to_scan):
            context = get_code_snippet(lines=line_getter.lines, line_number=line_number + 1)
            identified_secrets = call_function_with_arguments(
                plugin.analyze_line,
                filename=secret.filename,
                line=line,
                line_number=line_number + 1,

                # We enable eager search, because we *know* there's a secret here -- the baseline
                # flagged it after all.
                enable_eager_search=bool(secret.line_number),
                context=context,
            )

            for identified_secret in (identified_secrets or []):
                if identified_secret == secret:
                    all_secrets.append(identified_secret)

        if (
            len(all_secrets) == 0 and
            is_first_time_opening_file and
            not line_getter.use_eager_transformers
        ):
            line_getter.use_eager_transformers = True
        else:
            return all_secrets


class LineGetter:
    """
    The problem we try to address with this class is to cache the lines of a transformed file,
    without knowing beforehand what type of transformation that file needs to undergo.

    When we scan the file, we iterate through the transformed lines, in hopes of finding a
    secret. If we do find something, we can break out of that iterator, and move on to the next
    file.

    However, when we audit the file, we *know* that we've found a secret in this location before
    -- we just don't know what type of transformation the file had underwent to get there. As
    such, we need to try all transformations until we find the secret that the scan told us about.
    Once we find it, we should cache the results of that transformation so other audits of the
    same file will be smoother.

    We do this through a self-invalidating cache (self.lines), when we change the mode to using
    eager transformers.
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename

        self._lines: Optional[List[str]] = None
        self._raw_lines: Optional[List[str]] = None
        self._use_eager_transformers = False

    @contextmanager
    def open_file(self) -> Iterator[NamedIO]:
        """This is split up into a different function, so it can be overridden if necessary."""
        with open(self.filename) as f:
            yield cast(NamedIO, f)

    @property
    def lines(self) -> List[str]:
        if self._lines:
            return self._lines

        with self.open_file() as f:
            lines = get_transformed_file(f, use_eager_transformers=self.use_eager_transformers)
            self._lines = self.raw_lines if not lines else lines

        return self._lines

    @property
    def raw_lines(self) -> List[str]:
        if self._raw_lines:
            return self._raw_lines

        with self.open_file() as f:
            self._raw_lines = [line.rstrip() for line in f.readlines()]

        return self._raw_lines

    @property
    def has_cached_lines(self) -> bool:
        return bool(self._lines)

    @property
    def use_eager_transformers(self) -> bool:
        return self._use_eager_transformers

    @use_eager_transformers.setter
    def use_eager_transformers(self, status: bool) -> None:
        if status == self.use_eager_transformers:
            return

        self._use_eager_transformers = status
        self._lines = None              # invalidate cache
