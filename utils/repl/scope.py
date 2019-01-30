# -*- coding: utf-8 -*-

import inspect


class Scope:
    """Class that represents a global or local scope for both inspection and creation.

    Many REPL functions expect or return this class.
    """

    __slots__ = ('globals', 'locals')

    def __init__(self, _globals: dict = None, _locals: dict = None):
        self.globals: dict = _globals or {}
        self.locals: dict = _locals or {}

    def clean(self):
        """Clears out keys starting with an underscore.

        This reduces cross-eval pollution by removing private variables.
        """

        def _clean(scope):
            for key in tuple(scope.keys()):
                if key.startswith('_') and not key.startswith('__'):
                    del scope[key]

        _clean(self.globals)
        _clean(self.locals)

        return self

    def update(self, other):
        """Updates this scope with the content of another scope."""

        self.globals.update(other.globals)
        self.locals.update(other.locals)
        return self

    def update_globals(self, other: dict):
        """Updates the scope's globals with another dict."""

        self.globals.update(other)
        return self

    def update_locals(self, other: dict):
        """Updates the scope's locals with another dict."""

        self.locals.update(other)
        return self


def get_parent_scope_from_var(name, global_ok=False, skip_frames=0):
    """Iterates up the frame stack looking for a frame-scope containing the given variable name."""

    stack = inspect.stack()
    try:
        for frame_info in stack[skip_frames + 1:]:
            frame = None

            try:
                frame = frame_info.frame
                if name in frame.f_locals or (global_ok and name in frame.f_globals):
                    return Scope(_globals=frame.f_globals, _locals=frame.f_locals)
            finally:
                del frame

    finally:
        del stack

    return None


def get_parent_var(name, global_ok=False, default=None, skip_frames=0):
    """Directly gets a variable from a parent frame-scope."""

    scope = get_parent_scope_from_var(name, global_ok, skip_frames + 1)
    if not scope:
        return default

    if name in scope.locals:
        return scope.locals.get(name, default)

    return scope.globals.get(name, default)
