from contextlib import suppress

from django.db.models import QuerySet
from django.utils.functional import cached_property
from rest_framework.serializers import (
    ListSerializer,
    SerializerMetaclass,
    Serializer,
    ALL_FIELDS, )
from rest_framework.utils.serializer_helpers import BindingDict

from .exceptions import ChildNotSupported
from .utils import get_prefetch_select, process_field_options, get_relational_fields

try:
    from django.db.models import LOOKUP_SEP
except ImportError:
    LOOKUP_SEP = "__"


class mcls_cached_property(object):
    def __init__(self, func):
        self.func = func

    def __get__(self, instance, cls):
        if instance is None:
            return self
        value = self.func(instance)
        setattr(instance, self.func.__name__, value)
        return value


class DynamicReadSerializerMeta(SerializerMetaclass):
    @mcls_cached_property
    def all_select_prefetch(cls):
        return cls().evaluate_select_prefetch()

    def with_select_prefetch(
        cls,
        queryset,
        *args,
        apply_select=True,
        apply_prefetch=True,
        filter_fields=None,
        omit_fields=None,
        **kwargs,
    ):
        select, prefetch = get_prefetch_select(
            cls,
            filter_fields or (),
            omit_fields or (),
        )
        if apply_select is True:
            queryset = queryset.select_related(*select)
        if apply_prefetch is True:
            queryset = queryset.prefetch_related(*prefetch)

        return cls(
            queryset,
            *args,
            filter_fields=filter_fields,
            omit_fields=omit_fields,
            **kwargs,
        )


class DynamicReadSerializerMixin(metaclass=DynamicReadSerializerMeta):
    """
    A base serializer mixin that takes some additional arguments that controls
    which fields should be displayed.
    """

    def __init__(
        self, *args, filter_fields=None, omit_fields=None, **kwargs,
    ):
        """
        Overrides the original __init__ to support disabling dynamic flex fields.

        :param args:
        :param kwargs:
        :param filter_fields: This represents list of fields that should be allowed for serialization
        :param omit_fields: This represents list of fields that shouldn't be allowed for serialization
        :param optimize_queryset: boolean to enable/disable queryset optimizations

        """

        assert not bool(
            filter_fields and omit_fields,
        ), "Pass either filter_fields or omit_fields, not both"

        # type casting to tuple
        filter_fields, omit_fields = (
            tuple() if not filter_fields else tuple(filter_fields),
            tuple() if not omit_fields else tuple(omit_fields),
        )

        self._filter_fields = filter_fields
        self._omit_fields = omit_fields
        self.dr_meta = (
            process_field_options(filter_fields, omit_fields)
            if filter_fields or omit_fields
            else None
        )
        super().__init__(*args, **kwargs)

    def extract_serializer_from_child(self, child):
        """Child object can be a ListSerializer, PresentablePrimaryKeyRelatedField, etc. This method is responsible to
        return a DynamicReadSerializerMixin object(desired child), Override this to handle additional types of child and
        perform a super call if you want the ListSerializer child type to be handled if input child type is not known
        exit raising ChildNotSupported exception."""
        if isinstance(child, DynamicReadSerializerMixin):
            return child

        if isinstance(child, ListSerializer) and isinstance(
            child.child, DynamicReadSerializerMixin,
        ):
            return child.child

        raise ChildNotSupported(child)

    def is_child_multiple(self, child):
        return isinstance(child, ListSerializer)

    def derive_desired_fields(self, fields_map) -> set:
        field_names = set(fields_map.keys())

        # derive final set of field names wrt fields, omit
        if self.dr_meta["omit"]:
            desired_field_names = field_names - self.dr_meta["omit"]
        else:
            desired_field_names = (
                field_names
                if self.dr_meta["fields"] == ALL_FIELDS
                else self.dr_meta["fields"].intersection(field_names)
            )

        # attach dr_meta to necessary children
        for field, field_meta in self.dr_meta["nested"].items():
            with suppress(ChildNotSupported, KeyError):
                nested_field = self.extract_serializer_from_child(fields_map[field])

                if nested_field.dr_meta is None:
                    nested_field.dr_meta = field_meta

        return desired_field_names

    @cached_property
    def fields(self):
        """
        A dictionary of {field_name: field_instance}.
        Overridden method to support dynamic selection of fields during serialization
        check rest_framework.serializers.Serializer.fields for source definition
        """
        # `fields` is evaluated lazily. We do this to ensure that we don't
        # have issues importing modules that use ModelSerializers as fields,
        # even if Django's app-loading stage has not yet run.

        fields = BindingDict(self)
        fields_map = self.get_fields()
        field_names = (
            self.derive_desired_fields(fields_map)
            if self.dr_meta
            else set(fields_map.keys())
        )
        for field in field_names:
            fields[field] = fields_map[field]
        return fields

    def evaluate_select_prefetch(self, accessor_prefix=""):
        final_select = []
        final_prefetch = []
        relational_fields = get_relational_fields(self.__class__)

        fields = self.fields
        for field_name in relational_fields:
            if field_name not in fields:
                continue

            field_obj = fields[field_name]
            is_many = self.is_child_multiple(field_obj)

            with suppress(ChildNotSupported):
                field_obj = self.extract_serializer_from_child(field_obj)
                (
                    sub_select_related,
                    sub_prefetch_related,
                ) = field_obj.evaluate_select_prefetch(
                    accessor_prefix=f"{accessor_prefix}{field_name}{LOOKUP_SEP}",
                )
                if sub_select_related:
                    if not is_many:
                        final_select.extend(sub_select_related)
                    else:
                        final_prefetch.extend(sub_select_related)
                elif not is_many:
                    final_select.append(f"{accessor_prefix}{field_name}")
                if sub_prefetch_related:
                    final_prefetch.extend(sub_prefetch_related)
                elif is_many:
                    final_prefetch.append(f"{accessor_prefix}{field_name}")

        return final_select, final_prefetch
