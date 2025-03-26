from django.db import models
import json

class Changeset(models.Model):
    changeset_id = models.BigIntegerField(unique=True)
    created_at = models.DateTimeField(null=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    open = models.BooleanField(null=True)
    changes_count = models.IntegerField(null=True)
    user = models.CharField(max_length=100, null=True)
    user_id = models.IntegerField(null=True)
    min_lat = models.FloatField(null=True)
    max_lat = models.FloatField(null=True)
    min_lon = models.FloatField(null=True)
    max_lon = models.FloatField(null=True)
    comments_count = models.IntegerField(null=True)
    tags = models.JSONField(null=True)
    
    # New dedicated columns for common tags
    created_by = models.CharField(max_length=255, null=True, blank=True)
    created_by_family = models.CharField(max_length=255, null=True, blank=True)  # Base name of created_by (e.g., "StreetComplete")
    comment = models.TextField(null=True, blank=True)
    locale = models.CharField(max_length=50, null=True, blank=True)
    source = models.CharField(max_length=255, null=True, blank=True)
    imagery_used = models.JSONField(null=True, blank=True)  # Store as array of strings
    host = models.CharField(max_length=255, null=True, blank=True)
    changesets_count = models.IntegerField(null=True, blank=True)
    hashtags = models.JSONField(null=True, blank=True)  # Store as array of strings
    streetcomplete_quest_type = models.CharField(max_length=255, null=True, blank=True)
    review_requested = models.BooleanField(null=True, blank=True)
    remaining_tags = models.JSONField(null=True, blank=True)  # Store all other tags

    @property
    def imagery_list(self):
        """Returns a list of imageries used in this changeset"""
        return self.imagery_used or []

    @property
    def hashtags_list(self):
        """Returns a list of hashtags used in this changeset"""
        return self.hashtags or []

    @property
    def remaining_tags_dict(self):
        """Returns a dictionary of remaining tags"""
        return self.remaining_tags or {}

    @property
    def tags_dict(self):
        """Returns a dictionary of all tags"""
        return self.tags or {}
