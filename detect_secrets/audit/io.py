"""
Responsible for input/output for audit related functions.
"""
import os
import platform
import sys
from enum import Enum

from ..custom_types import SecretContext
from ..util.color import AnsiColor
from ..util.color import colorize


def print_message(message: str) -> None:
    print(message)


def print_error(message: str) -> None:
    print(message, file=sys.stderr)


def clear_screen() -> None:     # pragma: no cover
    command = 'clear'
    if platform.system() == 'Windows':
        command = 'cls'
    os.system(command)


def print_context(context: SecretContext) -> None:
    if not context.snippet:     # pragma: no cover
        raise ValueError('You should be using `print_secret_not_found` instead.')

    _print_header(context)

    context.snippet.add_line_numbers()
    if context.secret.secret_value:
        context.snippet.highlight_line(context.secret.secret_value)
    else:
        context.snippet.target_line = colorize(context.snippet.target_line, AnsiColor.BOLD)
    print_message(str(context.snippet))

    print_message('-' * 10)


def print_secret_not_found(context: SecretContext) -> None:
    if context.snippet:     # pragma: no cover
        raise ValueError(
            'Are you sure you want to do this? The secret *was* found in this context. '
            'If you are certain you want to override this behavior, be sure to null out '
            'the `context.snippet` value.',
        )

    _print_header(context)

    print_message(str(context.error))
    print_message('-' * 10)


def _print_header(context: SecretContext) -> None:      # pragma: no cover
    print_message(
        '{secret} {current_count} {of} {total_count}'.format(
            secret=colorize('Secret:     ', AnsiColor.BOLD),
            current_count=colorize(str(context.current_index), AnsiColor.PURPLE),
            of=colorize('of', AnsiColor.BOLD),
            total_count=colorize(str(context.num_total_secrets), AnsiColor.PURPLE),
        ),
    )
    print_message(
        '{prefix} {filename}'.format(
            prefix=colorize('Filename:   ', AnsiColor.BOLD),
            filename=colorize(context.secret.filename, AnsiColor.PURPLE),
        ),
    )
    print_message(
        '{prefix} {secret_type}'.format(
            prefix=colorize('Secret Type:', AnsiColor.BOLD),
            secret_type=colorize(context.secret.type, AnsiColor.PURPLE),
        ),
    )
    print_message('-' * 10)

    if context.header:
        print(context.header)


def get_user_decision(
    prompt_secret_decision: bool = True,
    can_step_back: bool = False,
) -> 'InputOptions':
    """
    :param prompt_secret_decision: if False, won't ask to label secret.
        e.g. if the secret isn't found on the line
    """
    prompter = UserPrompt(allow_labelling=prompt_secret_decision, allow_backstep=can_step_back)

    user_input = None
    while user_input not in prompter.valid_input:
        if user_input:
            print('Invalid input.')     # type: ignore # Statement unreachable? Come on mypy...

        user_input = input(str(prompter))
        if user_input:
            user_input = user_input[0].upper()

    return InputOptions(user_input)


class InputOptions(Enum):
    YES = 'Y'
    NO = 'N'
    SKIP = 'S'
    BACK = 'B'
    QUIT = 'Q'


class UserPrompt:
    def __init__(self, allow_labelling: bool, allow_backstep: bool) -> None:
        options = []
        if allow_labelling:
            options += [InputOptions.YES, InputOptions.NO]

        options.append(InputOptions.SKIP)
        if allow_backstep:
            options.append(InputOptions.BACK)

        options.append(InputOptions.QUIT)

        self.valid_input = {option.name[0] for option in options}
        self.options = [option.name.lower() for option in options]

    def __str__(self) -> str:
        if 'Y' in self.valid_input:
            output = 'Is this a secret that should be committed to this repository?'
        else:
            output = 'What would you like to do?'

        options = ', '.join([f'({option[0]}){option[1:]}' for option in self.options])

        return output + ' ' + options + ': '
