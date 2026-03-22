from rest_framework import serializers
from .models import Changeset


class ChangesetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Changeset
        fields = [
            'changeset_id', 'created_at', 'closed_at', 'open', 'changes_count',
            'user', 'user_id', 'min_lat', 'max_lat', 'min_lon', 'max_lon',
            'comments_count', 'created_by', 'created_by_family', 'comment',
            'locale', 'source', 'imagery_used', 'hashtags',
            'streetcomplete_quest_type', 'review_requested', 'changesets_count',
            'remaining_tags',
        ]
