from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Tuple

from ..constants import VerifiedResult
from .common import get_baseline_from_file
from .common import get_raw_secrets_from_file
from .common import LineGetter
from .common import open_file


class SecretClassToPrint(Enum):
    REAL_SECRET = 1
    FALSE_POSITIVE = 2

    @staticmethod
    def from_class(secret_class: VerifiedResult) -> 'SecretClassToPrint':
        if secret_class in [VerifiedResult.UNVERIFIED, VerifiedResult.VERIFIED_TRUE]:
            return SecretClassToPrint.REAL_SECRET
        else:
            return SecretClassToPrint.FALSE_POSITIVE


def generate_report(
    baseline_file: str,
    class_to_print: SecretClassToPrint | None = None,
    line_getter_factory: Callable[[str], 'LineGetter'] = open_file,
) -> Dict[str, List[Dict[str, Any]]]:

    secrets: Dict[Tuple[str, str], Any] = {}
    for filename, secret in get_baseline_from_file(baseline_file):
        verified_result = VerifiedResult.from_secret(secret)
        if (
            class_to_print is not None and
            SecretClassToPrint.from_class(verified_result) != class_to_print
        ):
            continue
        # Removal of the stored line number is required to force the complete file scanning to obtain all the secret occurrences. # noqa: E501
        secret.line_number = 0
        detections = get_raw_secrets_from_file(secret)
        line_getter = line_getter_factory(filename)
        for detection in detections:
            if (secret.secret_hash, filename) in secrets:
                secrets[(secret.secret_hash, filename)]['lines'][detection.line_number] = line_getter.lines[detection.line_number - 1]  # noqa: E501
                if secret.type not in secrets[(secret.secret_hash, filename)]['types']:
                    secrets[(secret.secret_hash, filename)]['types'].append(secret.type)
                secrets[(secret.secret_hash, filename)]['category'] = get_prioritized_verified_result(  # noqa: E501
                    verified_result,
                    VerifiedResult[secrets[(secret.secret_hash, filename)]['category']],
                ).name
            else:
                secrets[(secret.secret_hash, filename)] = {
                    'secrets': detection.secret_value,
                    'filename': filename,
                    'lines': {
                        detection.line_number: line_getter.lines[detection.line_number - 1],
                    },
                    'types': [
                        secret.type,
                    ],
                    'category': verified_result.name,
                }
    return {
        'results': list(secrets.values()),
    }


def get_prioritized_verified_result(
    result1: VerifiedResult,
    result2: VerifiedResult,
) -> VerifiedResult:
    if result1.value > result2.value:
        return result1
    else:
        return result2
