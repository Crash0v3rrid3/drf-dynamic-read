from collections import defaultdict
from contextlib import suppress
from functools import lru_cache

try:
    from django.db.models import LOOKUP_SEP
except ImportError:
    LOOKUP_SEP = "__"

from rest_framework.serializers import ALL_FIELDS


@lru_cache(maxsize=2048)
def get_prefetch_select(serializer_class, filter_fields: tuple, omit_fields: tuple):
    final_select, final_prefetch = [], []
    (
        all_select,
        all_prefetch,
    ) = serializer_class.all_select_prefetch  # fetch from cache

    if not (filter_fields or omit_fields):
        return all_select, all_prefetch

    if filter_fields:
        for field in filter_fields:
            final_select.extend(
                (
                    each
                    for each in all_select
                    if each.startswith(field) or field.startswith(each)
                )
            )
            final_prefetch.extend(
                (
                    each
                    for each in all_prefetch
                    if each.startswith(field) or field.startswith(each)
                )
            )

    else:
        if not final_select:
            final_select = all_select
        if not final_prefetch:
            final_prefetch = all_prefetch

        final_select = [
            each
            for each in final_select
            if not any(
                (
                    (each.startswith(field) or field.startswith(each))
                    for field in omit_fields
                )
            )
        ]

        final_prefetch = [
            each
            for each in final_prefetch
            if not any(
                (
                    (each.startswith(field) or field.startswith(each))
                    for field in omit_fields
                )
            )
        ]

    return final_select, final_prefetch


def dynamic_read_meta() -> dict:
    return dict(
        fields=set(), omit=set(), nested=defaultdict(dynamic_read_meta)
    )


@lru_cache()
def process_field_options(filter_fields: tuple, omit_fields: tuple) -> dict:
    filter_fields, omit_fields, dr_meta = (
        (each.split(LOOKUP_SEP) for each in filter_fields),
        (each.split(LOOKUP_SEP) for each in omit_fields),
        dynamic_read_meta(),
    )

    for field_list in filter_fields:
        parent_info = None
        for field in field_list:
            if not parent_info:
                field_meta = dr_meta
            else:
                parent_field, parent_meta = parent_info
                field_meta = parent_meta["nested"][parent_field]
            field_meta["fields"].add(field)
            parent_info = field, field_meta

    for field_list in omit_fields:
        parent_info = None
        for field in field_list:
            if not parent_info:
                field_meta = dr_meta
            else:
                parent_field, parent_meta = parent_info
                field_meta = parent_meta["nested"][parent_field]
            field_meta["fields"] = ALL_FIELDS
            parent_info = field, field_meta

        with suppress(IndexError):
            field_to_omit = field_list[-1]
            parent_info[1]["omit"].add(field_to_omit)

    return dr_meta


@lru_cache(maxsize=2048)
def get_relational_fields(cls):
    return {
        (hasattr(each, "get_accessor_name") and each.get_accessor_name())
        or each.name: each
        for each in cls.Meta.model._meta.get_fields()
        if each.is_relation
    }
