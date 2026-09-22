"""
Monkey-patch Django template context for Python 3.14 compatibility.
'__copy__' uses super().__dict__ which is removed in Python 3.14.
"""
import django.template.context as ctx
import copy


_original_copy = ctx.BaseContext.__copy__


def _patched_copy(self):
    cls = self.__class__
    result = cls.__new__(cls)
    for k, v in self.__dict__.items():
        if k.startswith('_') and hasattr(result, k):
            continue
        try:
            setattr(result, k, copy.copy(v))
        except Exception:
            setattr(result, k, v)
    return result


ctx.BaseContext.__copy__ = _patched_copy
