"""
Defines the interfaces for extending plugins.

In most cases, you probably can just use the RegexBasedPlugin. In more advanced cases,
you can also use the LineBasedPlugin, and FileBasedPlugin. If you're extending the BasePlugin,
things may not work as you expect (see the scan logic in SecretsCollection).
"""
from __future__ import annotations

import re
from abc import ABCMeta
from abc import abstractmethod
from abc import abstractproperty
from typing import Any
from typing import Dict
from typing import Generator
from typing import Iterable
from typing import Pattern
from typing import Set

import requests

from ..constants import VerifiedResult
from ..core.potential_secret import PotentialSecret
from ..settings import get_settings
from detect_secrets.util.code_snippet import CodeSnippet
from detect_secrets.util.inject import call_function_with_arguments


class BasePlugin(metaclass=ABCMeta):
    @abstractproperty
    def secret_type(self) -> str:
        """
        Unique, user-facing description to identify this type of secret. This should be overloaded
        by declaring a class variable (rather than a `property` function), since we need to know
        a plugin's `secret_type` before initialization.

        NOTE: Choose carefully! If this value is changed, it will require old baselines to be
        updated to use the new secret type.
        """
        raise NotImplementedError

    @abstractmethod
    def analyze_string(self, string: str) -> Generator[str, None, None]:
        """Yields all the raw secret values within a supplied string."""
        raise NotImplementedError

    def analyze_line(
        self,
        filename: str,
        line: str,
        line_number: int = 0,
        context: CodeSnippet | None = None,
        raw_context: CodeSnippet | None = None,
        **kwargs: Any
    ) -> Set[PotentialSecret]:
        """This examines a line and finds all possible secret values in it."""
        output = set()
        for match in self.analyze_string(line, **kwargs):
            is_verified: bool = False
            # If the filter is disabled it means --no-verify flag was passed
            # We won't run verification in that case
            if (
                'detect_secrets.filters.common.is_ignored_due_to_verification_policies'
                in get_settings().filters
            ):
                try:
                    verified_result = call_function_with_arguments(
                        self.verify,
                        secret=match,
                        context=context,
                        raw_context=raw_context,
                    )
                    is_verified = True if verified_result == VerifiedResult.VERIFIED_TRUE else False
                except requests.exceptions.RequestException:
                    is_verified = False

            output.add(
                PotentialSecret(
                    type=self.secret_type,
                    filename=filename,
                    secret=match,
                    line_number=line_number,
                    is_verified=is_verified,
                ),
            )

        return output

    def verify(self, secret: str) -> VerifiedResult:
        return VerifiedResult.UNVERIFIED

    def json(self) -> Dict[str, Any]:
        return {
            'name': self.__class__.__name__,
        }

    def format_scan_result(self, secret: PotentialSecret) -> str:
        try:
            verification_level = VerifiedResult(
                get_settings().filters[
                    'detect_secrets.filters.common.is_ignored_due_to_verification_policies'
                ]['min_level'],
            )
        except KeyError:
            verification_level = VerifiedResult.VERIFIED_FALSE

        if verification_level == VerifiedResult.VERIFIED_FALSE:
            # This is a secret, but we can't verify it. So this is the best we can do.
            return 'True'

        if not secret.secret_value and not secret.is_verified:
            # If the secret isn't verified, but we don't know the true secret value, this
            # is also the best we can do.
            return 'True  (unverified)'

        if not secret.is_verified:
            try:
                # NOTE: There is no context here, since in this frame, we're only aware of the
                # secret itself.
                verified_result = self.verify(secret.secret_value)      # type: ignore
            except (requests.exceptions.RequestException, TypeError):
                # NOTE: A TypeError is raised when the function expects a `context` to be supplied.
                # However, if this function is run through a context-less situation (e.g. adhoc
                # string scanning), we don't have that context to provide. As such, the secret is
                # UNVERIFIED.
                verified_result = VerifiedResult.UNVERIFIED
        else:
            # It's not going to be VERIFIED_FALSE, otherwise, we won't have the secret object
            # to format.
            verified_result = VerifiedResult.VERIFIED_TRUE

        return {
            # This will only occur if the verification process happens in this formatting step.
            VerifiedResult.VERIFIED_FALSE: 'False (verified)',

            # This occurs either if we've already known the secret is verified, or that the
            # verification process that just occurred proved it valid.
            VerifiedResult.VERIFIED_TRUE: 'True  (verified)',

            # Sometimes, the plugin may not have defined a verification process.
            VerifiedResult.UNVERIFIED: 'True  (unverified)',
        }[verified_result]

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, BasePlugin):
            raise NotImplementedError

        return self.json() == other.json()


class RegexBasedDetector(BasePlugin, metaclass=ABCMeta):
    """Parent class for regular-expression based detectors.

    To create a new regex-based detector, subclass this and set `secret_type` with a
    description and `denylist` with a sequence of *compiled* regular expressions, like:

    class FooDetector(RegexBasedDetector):

        secret_type = "foo"

        denylist = (
            re.compile(r'foo'),
        )
    """
    @abstractproperty
    def denylist(self) -> Iterable[Pattern]:
        raise NotImplementedError

    def analyze_string(self, string: str) -> Generator[str, None, None]:
        for regex in self.denylist:
            for match in regex.findall(string):
                if isinstance(match, tuple):
                    for submatch in filter(bool, match):
                        # It might make sense to paste break after yielding
                        yield submatch
                else:
                    yield match

    @staticmethod
    def build_assignment_regex(
        prefix_regex: str,
        secret_keyword_regex: str,
        secret_regex: str,
    ) -> Pattern:
        """Generate assignment regex
        It reads 3 input parameters, each stands for regex. The return regex would look for
        secret in following format.
        <prefix_regex>(-|_|)<secret_keyword_regex> <assignment> <secret_regex>
        assignment would include =,:,:=,::
        keyname and value supports optional quotes
        """
        begin = r'(?:(?<=\W)|(?<=^))'
        opt_quote = r'(?:"|\'|)'
        opt_open_square_bracket = r'(?:\[|)'
        opt_close_square_bracket = r'(?:\]|)'
        opt_dash_underscore = r'(?:_|-|)'
        opt_space = r'(?: *)'
        assignment = r'(?:=|:|:=|=>| +|::)'
        return re.compile(
            r'{begin}{opt_open_square_bracket}{opt_quote}{prefix_regex}{opt_dash_underscore}'
            '{secret_keyword_regex}{opt_quote}{opt_close_square_bracket}{opt_space}'
            '{assignment}{opt_space}{opt_quote}{secret_regex}{opt_quote}'.format(
                begin=begin,
                opt_open_square_bracket=opt_open_square_bracket,
                opt_quote=opt_quote,
                prefix_regex=prefix_regex,
                opt_dash_underscore=opt_dash_underscore,
                secret_keyword_regex=secret_keyword_regex,
                opt_close_square_bracket=opt_close_square_bracket,
                opt_space=opt_space,
                assignment=assignment,
                secret_regex=secret_regex,
            ), flags=re.IGNORECASE,
        )
