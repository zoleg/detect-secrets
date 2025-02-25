from __future__ import annotations

import math
import re
import string
from abc import ABCMeta
from contextlib import contextmanager
from typing import Any
from typing import cast
from typing import Dict
from typing import Generator
from typing import Set

from ..core.potential_secret import PotentialSecret
from .base import BasePlugin
from detect_secrets.util.code_snippet import CodeSnippet


class HighEntropyStringsPlugin(BasePlugin, metaclass=ABCMeta):
    """Base class for string pattern matching."""

    def __init__(self, charset: str, limit: float) -> None:
        if limit < 0 or limit > 8:
            raise ValueError(
                'The limit set for HighEntropyStrings must be between 0.0 and 8.0',
            )

        self.charset = charset
        self.entropy_limit = limit

        # We require quoted strings to reduce noise.
        # NOTE: We need this to be a capturing group, so back-reference can work.
        self.regex = re.compile(r'([\'"])([{}]+)(\1)'.format(re.escape(charset)))

    def analyze_string(self, string: str) -> Generator[str, None, None]:
        for result in self.regex.findall(string):
            if isinstance(result, tuple):
                # This occurs on the default regex, but not on the eager regex.
                result = result[1]

            # We perform the shannon entropy check in `analyze_line` instead, so that we have
            # more control over **when** we display the results of this plugin. Specifically,
            # this allows us to show the computed entropy values during adhoc string scans.
            yield result

    def analyze_line(
        self,
        filename: str,
        line: str,
        line_number: int = 0,
        context: CodeSnippet | None = None,
        raw_context: CodeSnippet | None = None,
        enable_eager_search: bool = False,
        **kwargs: Any,
    ) -> Set[PotentialSecret]:
        output = super().analyze_line(
            filename=filename,
            line=line,
            line_number=line_number,
            context=context,
            raw_context=raw_context,
        )
        if output or not enable_eager_search:
            # NOTE: We perform the limit filter at this layer (rather than analyze_string) so
            # that we can surface secrets that do not meet the limit criteria when
            # enable_eager_search=True.
            return {
                secret
                for secret in (output or set())
                if (
                    self.calculate_shannon_entropy(cast(str, secret.secret_value)) >
                    self.entropy_limit
                )
            }

        # This is mainly used for adhoc string scanning. As such, it's just bad UX to require
        # quotes around the expected secret. In these cases, we only try to search it without
        # requiring quotes when we can't find any results *with* quotes.
        #
        # NOTE: Since we currently assume this is only used for adhoc string scanning, we
        # perform the limit filtering outside this function. This allows us to see *why* secrets
        # have failed to be caught with our configured limit.
        with self.non_quoted_string_regex(is_exact_match=False):
            return super().analyze_line(
                filename=filename,
                line=line,
                line_number=line_number,
                raw_context=raw_context,
            )

    def calculate_shannon_entropy(self, data: str) -> float:
        """Returns the entropy of a given string.

        Borrowed from: http://blog.dkbza.org/2007/05/scanning-data-for-entropy-anomalies.html.
        """
        if not data:  # pragma: no cover
            return 0

        entropy = 0.0
        for x in self.charset:
            p_x = float(data.count(x)) / len(data)
            if p_x > 0:
                entropy += - p_x * math.log(p_x, 2)

        return entropy

    def format_scan_result(self, secret: PotentialSecret) -> str:
        if not secret.secret_value:
            # This is the best we can do, since we don't have the raw value to process.
            return 'True'

        entropy = round(self.calculate_shannon_entropy(secret.secret_value), 3)
        if entropy < self.entropy_limit:
            return f'False ({entropy})'

        return f'True  ({entropy})'

    def json(self) -> Dict[str, Any]:
        return {
            **super().json(),
            'limit': self.entropy_limit,
        }

    @contextmanager
    def non_quoted_string_regex(self, is_exact_match: bool = True) -> Generator[None, None, None]:
        """
        For certain file formats, strings need not necessarily follow the
        normal convention of being denoted by single or double quotes. In these
        cases, we modify the regex accordingly.

        :param is_exact_match: True if you need to scan the secret directly.
            However, if the secret is part of a line of text, and you want to find the
            secret within the line, use False.
        """
        old_regex = self.regex

        regex_alternative = r'([{}]+)'.format(re.escape(self.charset))
        if is_exact_match:
            regex_alternative = r'^' + regex_alternative + r'$'

        self.regex = re.compile(regex_alternative)

        try:
            yield
        finally:
            self.regex = old_regex


class Base64HighEntropyString(HighEntropyStringsPlugin):
    """Scans for random-looking base64 encoded strings."""
    secret_type = 'Base64 High Entropy String'

    def __init__(self, limit: float = 4.5) -> None:
        super().__init__(
            charset=(
                string.ascii_letters
                + string.digits
                + '+/'  # Regular base64
                + '\\-_'  # Url-safe base64
                + '='  # Padding
            ),
            limit=limit,
        )


class HexHighEntropyString(HighEntropyStringsPlugin):
    """Scans for random-looking hex encoded strings."""

    secret_type = 'Hex High Entropy String'

    def __init__(self, limit: float = 3.0) -> None:
        super().__init__(
            charset=string.hexdigits,
            limit=limit,
        )

    def calculate_shannon_entropy(self, data: str) -> float:
        """
        In our investigations, we have found that when the input is all digits,
        the number of false positives we get greatly exceeds realistic true
        positive scenarios.

        Therefore, this tries to capture this heuristic mathematically.

        We do this by noting that the maximum shannon entropy for this charset
        is ~3.32 (e.g. "0123456789", with every digit different), and we want
        to lower that below the standard limit, 3. However, at the same time,
        we also want to accommodate the fact that longer strings have a higher
        chance of being a true positive, which means "01234567890123456789"
        should be closer to the maximum entropy than the shorter version.
        """
        entropy = super().calculate_shannon_entropy(data)
        if len(data) == 1:
            return entropy

        try:
            # Check if str is that of a number
            int(data)

            # This multiplier was determined through trial and error, with the
            # intent of keeping it simple, yet achieving our goals.
            entropy -= 1.2 / math.log(len(data), 2)
        except ValueError:
            pass

        return entropy
