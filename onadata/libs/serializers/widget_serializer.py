from django.core.exceptions import ObjectDoesNotExist
from django.core.urlresolvers import resolve, get_script_prefix, Resolver404
from django.utils.translation import ugettext as _
from guardian.shortcuts import get_users_with_perms
from rest_framework import serializers
from rest_framework.compat import urlparse
from rest_framework.reverse import reverse

from django.http import Http404

from onadata.apps.logger.models.xform import XForm
from onadata.apps.logger.models.data_view import DataView
from onadata.apps.logger.models.widget import Widget
from onadata.libs.utils.string import str2bool
from onadata.libs.utils.chart_tools import get_field_from_field_name,\
    DATA_TYPE_MAP, get_field_label

from onadata.libs.utils.common_tags import SUBMISSION_TIME


def get_xform_from_widgets(widget):
    if isinstance(widget.content_object, XForm):
        xform = widget.content_object
    elif isinstance(widget.content_object, DataView):
        # must be a dataview
        xform = widget.content_object.xform

    return xform


def get_widget_column_field(widget):
    try:
        xform = get_xform_from_widgets(widget)
        field = get_field_from_field_name(widget.column,
                                          xform.data_dictionary())

        if isinstance(field, basestring) and field == SUBMISSION_TIME:
            # Create a dynamic class that behaves as a normal field
            def get_abbreviated_xpath(self):
                return '_submission_time'

            field_dict = {
                'label': 'Submission Time',
                'get_abbreviated_xpath': get_abbreviated_xpath,
                'type': 'datetime'
            }

            return type('pyxform.question.InputQuestion', (object,),
                        field_dict)()

        return field
    except Http404:
        return None


class GenericRelatedField(serializers.HyperlinkedRelatedField):
    default_error_messages = {
        'incorrect_match': _('`{input}` is not a valid relation.')
    }

    def __init__(self, *args, **kwargs):
        self.view_names = ['xform-detail', 'dataviews-detail']
        self.resolve = resolve
        self.reverse = reverse
        super(serializers.RelatedField, self).__init__(*args, **kwargs)

    def _setup_field(self, view_name):
        self.lookup_url_kwarg = self.lookup_field

        if view_name == 'xform-detail':
            self.queryset = XForm.objects.all()

        if view_name == 'dataviews-detail':
            self.queryset = DataView.objects.all()

    def to_representation(self, value):
        if isinstance(value, XForm):
            self.view_name = 'xform-detail'
        elif isinstance(value, DataView):
            self.view_name = 'dataviews-detail'
        else:
            raise Exception(_(u"Uknown type for content_object."))

        self._setup_field(self.view_name)

        return super(GenericRelatedField, self).to_representation(value)

    def to_internal_value(self, data):
        try:
            http_prefix = data.startswith(('http:', 'https:'))
        except AttributeError:
            self.fail('incorrect_type', data_type=type(data).__name__)
        input_data = data
        if http_prefix:
            # If needed convert absolute URLs to relative path
            data = urlparse.urlparse(data).path
            prefix = get_script_prefix()
            if data.startswith(prefix):
                data = '/' + data[len(prefix):]

        try:
            match = self.resolve(data)
        except Resolver404:
            self.fail('no_match')

        if match.view_name not in self.view_names:
            self.fail('incorrect_match', input=input_data)

        self._setup_field(match.view_name)

        try:
            return self.get_object(match.view_name, match.args, match.kwargs)
        except (ObjectDoesNotExist, TypeError, ValueError):
            self.fail('does_not_exist')

        return data


class WidgetSerializer(serializers.HyperlinkedModelSerializer):
    id = serializers.ReadOnlyField()
    url = serializers.HyperlinkedIdentityField(
        view_name='widgets-detail',
        lookup_field='pk'
    )
    content_object = GenericRelatedField()
    key = serializers.CharField(read_only=True)
    data = serializers.SerializerMethodField()
    order = serializers.IntegerField(required=False)
    field_type = serializers.SerializerMethodField()
    data_type = serializers.SerializerMethodField()
    field_xpath = serializers.SerializerMethodField()
    field_label = serializers.SerializerMethodField()

    class Meta:
        model = Widget
        fields = ('id', 'url', 'key', 'title', 'description', 'widget_type',
                  'order', 'view_type', 'column', 'group_by', 'content_object',
                  'data', 'aggregation', 'field_type', 'data_type',
                  'field_xpath', 'field_label')

    def get_data(self, obj):
        # Get the request obj
        request = self.context.get('request')

        # Check if data flag is present
        data_flag = request.GET.get('data')
        key = request.GET.get('key')

        if (str2bool(data_flag) or key) and obj:
            data = Widget.query_data(obj)
        else:
            data = []

        return data

    def validate(self, attrs):
        column = attrs.get('column')

        # Get the form
        if 'content_object' in attrs:
            content_object = attrs.get('content_object')

            if isinstance(content_object, XForm):
                xform = content_object
            elif isinstance(content_object, DataView):
                # must be a dataview
                xform = content_object.xform

            data_dictionary = xform.data_dictionary()

            if column not in data_dictionary.get_headers():
                raise serializers.ValidationError({
                    'column': _(u"'{}' not in the form.".format(column))
                })

        order = attrs.get('order')

        # Set the order
        if order:
            self.instance.to(order)

        return attrs

    def validate_content_object(self, value):
        request = self.context.get('request')
        users = get_users_with_perms(
            value.project, attach_perms=False, with_group_users=False
        )

        if request.user not in users:
            raise serializers.ValidationError(_(
                u"You don't have permission to the XForm."
            ))

        return value

    def get_field_type(self, obj):
        if obj:
            field = get_widget_column_field(obj)
            if field:
                return field.type
        return None

    def get_data_type(self, obj):
        if obj:

            field = get_widget_column_field(obj)
            if field:
                return DATA_TYPE_MAP.get(field.type, 'categorized')
        return None

    def get_field_xpath(self, obj):
        if obj:
            field = get_widget_column_field(obj)
            if field:
                return field.get_abbreviated_xpath()
        return None

    def get_field_label(self, obj):
        if obj:
            field = get_widget_column_field(obj)
            if field:
                return get_field_label(field)
        return None
