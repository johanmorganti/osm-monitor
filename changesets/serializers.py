from rest_framework import serializers


class ChangesetSerializer(serializers.Serializer):
    """One raw changeset record (/api/changesets/), read from the analytics
    backend's records (attribute access), not from a database model.
    changeset_id and created_at are always present; the rest may be null."""
    changeset_id = serializers.IntegerField()
    created_at = serializers.DateTimeField(allow_null=True)
    closed_at = serializers.DateTimeField(required=False, allow_null=True)
    open = serializers.BooleanField(required=False, allow_null=True)
    changes_count = serializers.IntegerField(required=False, allow_null=True)
    user = serializers.CharField(required=False, allow_null=True)
    user_id = serializers.IntegerField(required=False, allow_null=True)
    min_lat = serializers.FloatField(required=False, allow_null=True)
    max_lat = serializers.FloatField(required=False, allow_null=True)
    min_lon = serializers.FloatField(required=False, allow_null=True)
    max_lon = serializers.FloatField(required=False, allow_null=True)
    comments_count = serializers.IntegerField(required=False, allow_null=True)
    created_by = serializers.CharField(required=False, allow_null=True)
    created_by_family = serializers.CharField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_null=True)
    locale = serializers.CharField(required=False, allow_null=True)
    source = serializers.CharField(required=False, allow_null=True)
    imagery_used = serializers.JSONField(required=False, allow_null=True)
    hashtags = serializers.JSONField(required=False, allow_null=True)
    streetcomplete_quest_type = serializers.CharField(required=False, allow_null=True)
    review_requested = serializers.BooleanField(required=False, allow_null=True)
    changesets_count = serializers.IntegerField(required=False, allow_null=True)
    remaining_tags = serializers.JSONField(required=False, allow_null=True)
