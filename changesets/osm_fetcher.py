import logging
import os
from os import path
import requests
import xml.etree.ElementTree as ET
import gzip
from .models import Changeset
from datetime import datetime
from django.utils import timezone
from ddtrace import tracer
import json
import copy

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds; avoid hanging forever on a stalled connection

COLUMNS_MAPPING = {
    "id": "changeset_id",
    "created_at": "created_at",
    "closed_at": "closed_at",
    "open": "open",
    "num_changes": "changes_count",
    "user": "user",
    "uid": "user_id",
    "min_lat": "min_lat",
    "max_lat": "max_lat",
    "min_lon": "min_lon",
    "max_lon": "max_lon",
    "comments_count": "comments_count"
}


def urlized_sequence_number(sequence_number):
    
    # returns url of the form https://planet.osm.org/replication/changesets/123/456/789.osm.gz
    sequence_number_adjusted = str(sequence_number).rjust(9, "0")
    return f"https://planet.osm.org/replication/changesets/{sequence_number_adjusted[0:3]}/{sequence_number_adjusted[3:6]}/{sequence_number_adjusted[6:9]}.osm.gz"


def get_sequence_min_max_changeset_id(sequence_number, locally=False):
    # returns min and max values of changeset ids 
    if locally:
        sequence_path = "./source/" + str(sequence_number) + ".osm.gz"
        with open(sequence_path, 'rb') as sequence_file:
            xml_sequence = ET.fromstring(gzip.decompress(sequence_file.read()))
    else:
        url_sequence = urlized_sequence_number(sequence_number)
        xml_sequence_request = requests.get(url_sequence, stream=True, timeout=REQUEST_TIMEOUT).raw.read()
        xml_sequence = ET.fromstring(gzip.decompress(xml_sequence_request))
    return min(xml_sequence, key=lambda x: x.attrib['id']).attrib['id'], max(xml_sequence, key=lambda x: x.attrib['id']).attrib['id']


def process_sequence(sequence_number):
    span = tracer.trace("osm.process_sequence", service="osm-monitor", resource="process_sequence")
    span.set_tag("osm.sequence_number", sequence_number)
    try:
        _process_sequence_traced(sequence_number, span)
    except Exception:
        span.set_traceback()
        raise
    finally:
        span.finish()


def _process_sequence_traced(sequence_number, span):

    sequence_path = "./source/" + str(sequence_number) + ".osm.gz"
    if not path.isfile(sequence_path):
        sequence_was_fetched = True
        url_sequence = urlized_sequence_number(sequence_number)
        xml_sequence_request = requests.get(url_sequence, stream=True, timeout=REQUEST_TIMEOUT).raw.read()
        xml_sequence = ET.fromstring(gzip.decompress(xml_sequence_request))
        os.makedirs(path.dirname(sequence_path), exist_ok=True)
        with open(sequence_path, 'wb') as sequence_file:
            sequence_file.write(xml_sequence_request)
    else:
        sequence_was_fetched = False
        with open(sequence_path, 'rb') as sequence_file:
            xml_sequence = ET.fromstring(gzip.decompress(sequence_file.read()))

    log_extra = {'osm.sequence_number': sequence_number}
    created_count, skipped_count, updated_count = import_changeset_batch(list(xml_sequence), log_extra)

    source = "network" if sequence_was_fetched else "cache"
    logger.info(
        "Processed sequence",
        extra={
            **log_extra,
            'osm.source': source,
            'osm.changesets.created': created_count,
            'osm.changesets.skipped': skipped_count,
            'osm.changesets.updated': updated_count,
        },
    )

    span.set_tag("osm.source", source)
    span.set_metric("osm.changesets.created", created_count)
    span.set_metric("osm.changesets.skipped", skipped_count)
    span.set_metric("osm.changesets.updated", updated_count)


def _parse_changeset_element(changeset, log_extra):
    """
    Changeset overview :
        <changeset [...] attribute_key="attribute_value" [...]>
            [...]
            # list of elements, in OSM there is moslty tag elements, with k/v attributes for key/values
            <tag k="key" v="value"/>
            [...]
        </changeset>
        <osm>
        <changeset id="113928427" created_at="2021-11-18T06:17:42Z" open="false" comments_count="0" changes_count="6" closed_at="2021-11-18T06:17:44Z" min_lat="15.3384649" min_lon="-91.8697209" max_lat="15.3386183" max_lon="-91.8694203" uid="12026398" user="<redacted>">
        <tag k="changesets_count" v="73"/>
        [...] # More k,v tags
        </changeset>
        <changeset id="113928426" created_at="2021-11-18T06:17:42Z" open="false" comments_count="0" changes_count="11" closed_at="2021-11-18T06:17:43Z" min_lat="-23.6402734" min_lon="47.3068178" max_lat="-23.6387451" max_lon="47.3096663" uid="13571396" user="<redated>">
        <tag k="changesets_count" v="2200"/>
        [...] # More k,v tags
        </changeset>
        [...] # more changesets
        </osm>
    """
    changeset_to_add = {}
    changeset_to_add["tags"] = {}

    for attribute, value in changeset.attrib.items():
        if attribute in COLUMNS_MAPPING:
            if attribute == "open":
                value = value.lower() == 'true'
            elif attribute in ["changes_count", "comments_count", "user_id"]:
                value = int(value)
            elif attribute in ["min_lat", "max_lat", "min_lon", "max_lon"]:
                value = float(value)
            elif attribute in ["created_at", "closed_at"]:
                naive_datetime = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                value = timezone.make_aware(naive_datetime, timezone.utc) # prevents from RuntimeWarning about time zone

            changeset_to_add[COLUMNS_MAPPING[attribute]] = value
        else:
            logger.warning(
                "Unknown changeset attribute",
                extra={
                    **log_extra,
                    'osm.changeset_id': changeset.attrib["id"],
                    'osm.attribute': attribute,
                },
            )

    for element in changeset:
        if 'tag' in element.tag:
            if 'k' in element.attrib:
                tag_key = element.attrib["k"]
                tag_value = element.attrib["v"]

                # Store in tags JSON field
                changeset_to_add["tags"][tag_key] = tag_value

                # Populate dedicated columns for common tags
                if tag_key == 'created_by':
                    changeset_to_add['created_by'] = tag_value
                    # Extract family name with specific rules
                    if tag_value:
                        family = None
                        # Handle special cases first
                        if 'JOSM' in tag_value:
                            family = 'JOSM'
                        elif tag_value.startswith('Go Map!!'):
                            family = 'Go Map!!'
                        elif tag_value.startswith('OsmAnd'):
                            family = 'OsmAnd'
                        elif tag_value.startswith('StreetComplete'):
                            family = 'StreetComplete'
                        elif tag_value.startswith('Osm Go!'):
                            family = 'Osm Go!'
                        elif tag_value.startswith('https://'):
                            family = tag_value
                        else:
                            # For other cases, take up to first space, slash, or parenthesis
                            family = tag_value.split(' ')[0].split('/')[0].split('(')[0].strip()

                            # Special case adjustments
                            if family == 'AED':
                                family = 'AED Map'
                            elif family == 'Abakus':
                                family = 'StrazakOSM'
                            elif family == 'Every':
                                family = 'Every Door'
                            elif family == 'Organic':
                                family == 'Organic Maps'
                            elif family == 'Votre':
                                family = 'Ubiflow'
                            elif family == None:
                                family = 'Other'

                        changeset_to_add['created_by_family'] = family
                    else:
                        changeset_to_add['created_by_family'] = 'Other'
                elif tag_key == 'comment':
                    changeset_to_add['comment'] = tag_value
                elif tag_key == 'locale':
                    changeset_to_add['locale'] = tag_value
                    if tag_value:
                        changeset_to_add['locale_family'] = tag_value.replace('_', '-').split('-')[0].upper() or None
                elif tag_key == 'source':
                    changeset_to_add['source'] = tag_value
                elif tag_key == 'imagery_used':
                    # Split imageries into array and strip whitespace
                    imageries = [img.strip() for img in tag_value.split(';') if img.strip()]
                    changeset_to_add['imagery_used'] = imageries  # Store as Python list
                    if imageries:
                        raw = imageries[0]
                        if raw.startswith('http'):
                            from urllib.parse import urlparse
                            family = urlparse(raw).netloc or raw
                        else:
                            family = raw.split(' ')[0].split('/')[0].split('(')[0].strip()
                        changeset_to_add['imagery_family'] = family or None
                elif tag_key == 'host':
                    changeset_to_add['host'] = tag_value
                elif tag_key == 'changesets_count':
                    try:
                        changeset_to_add['changesets_count'] = int(tag_value)
                    except ValueError:
                        changeset_to_add['changesets_count'] = None
                elif tag_key == 'hashtags':
                    # Split hashtags into array and remove # symbol
                    hashtags = [tag.lstrip('#') for tag in tag_value.split(';') if tag]
                    changeset_to_add['hashtags'] = hashtags  # Store as Python list
                elif tag_key == 'StreetComplete:quest_type':
                    changeset_to_add['streetcomplete_quest_type'] = tag_value
                elif tag_key == 'review_requested':
                    changeset_to_add['review_requested'] = tag_value.lower() == 'yes'
                else:
                    # Store in remaining_tags if not in dedicated columns
                    if 'remaining_tags' not in changeset_to_add:
                        changeset_to_add['remaining_tags'] = {}
                    changeset_to_add['remaining_tags'][tag_key] = tag_value
        elif 'discussion' in element.tag:
            ## TODO : implement
            continue
        else:
            logger.warning(
                "Unexpected XML element under changeset",
                extra={
                    **log_extra,
                    'osm.changeset_id': changeset.attrib["id"],
                    'osm.element_tag': element.tag,
                },
            )

    return changeset_to_add


def import_changeset_batch(changeset_elements, log_extra):
    """Batched existence-check + insert/update for a list of <changeset> XML
    elements, from any source (a replication sequence file, or a slice of the
    full planet changesets dump). One query for the whole batch instead of
    one per changeset — costly once the DB is a separate networked Postgres
    instance rather than a same-process SQLite file.

    Returns (created_count, skipped_count, updated_count)."""
    existing_changes_count_by_id = dict(
        Changeset.objects.filter(
            changeset_id__in=[int(c.attrib['id']) for c in changeset_elements]
        ).values_list('changeset_id', 'changes_count')
    )

    changesets_to_create = []
    changeset_ids_to_delete = []
    skipped_count = 0
    updated_count = 0

    for changeset in changeset_elements:
        changeset_id = int(changeset.attrib['id'])

        # Check for duplicates and compare changes_count
        existing_changes_count = existing_changes_count_by_id.get(changeset_id)
        if existing_changes_count is not None:
            new_changes_count = int(changeset.attrib.get('num_changes', 0))
            if existing_changes_count >= new_changes_count:
                logger.debug(
                    "Changeset already up to date, skipping",
                    extra={
                        **log_extra,
                        'osm.changeset_id': changeset_id,
                        'osm.changes_count.existing': existing_changes_count,
                        'osm.changes_count.incoming': new_changes_count,
                    },
                )
                skipped_count += 1
                continue
            else:
                logger.debug(
                    "Changeset has grown, updating",
                    extra={
                        **log_extra,
                        'osm.changeset_id': changeset_id,
                        'osm.changes_count.existing': existing_changes_count,
                        'osm.changes_count.incoming': new_changes_count,
                    },
                )
                changeset_ids_to_delete.append(changeset_id)  # deleted in one batch below
                updated_count += 1

        changesets_to_create.append(_parse_changeset_element(changeset, log_extra))

    if changeset_ids_to_delete:
        Changeset.objects.filter(changeset_id__in=changeset_ids_to_delete).delete()

    if changesets_to_create:
        try:
            Changeset.objects.bulk_create(
                [Changeset(**changeset) for changeset in changesets_to_create],
                ignore_conflicts=True  # This will skip any duplicates that somehow made it through
            )
        except Exception:
            logger.exception(
                "Bulk creation failed, falling back to individual creation",
                extra=log_extra,
            )
            # Fallback to individual creation if bulk create fails
            for changeset in changesets_to_create:
                try:
                    Changeset.objects.create(**changeset)
                except Exception:
                    logger.exception(
                        "Error creating changeset",
                        extra={**log_extra, 'osm.changeset_id': changeset.get('changeset_id')},
                    )

    return len(changesets_to_create), skipped_count, updated_count


def fetch_and_process_changesets(seq_start, seq_end, on_progress=None):
    if seq_start > seq_end:
        seq_start, seq_end = seq_end, seq_start

    for sequence_number in range(seq_start, seq_end + 1):
        process_sequence(sequence_number)
        if on_progress:
            on_progress(sequence_number)

    # get min and max values of changeset ids
    min_changesets = get_sequence_min_max_changeset_id(seq_start)[0]
    max_changesets = get_sequence_min_max_changeset_id(seq_end)[1]

    return min_changesets, max_changesets


###### TESTING ######
# this is for debugging/testing
def duration_info(sequence_number):
    sequence_path = "./source/" + str(sequence_number) + ".osm.gz"
    if not path.isfile(sequence_path):
        url_sequence = urlized_sequence_number(sequence_number)
        xml_sequence_request = requests.get(url_sequence, stream=True, timeout=REQUEST_TIMEOUT).raw.read()
        xml_sequence = ET.fromstring(gzip.decompress(xml_sequence_request))
        with open(sequence_path, 'wb') as sequence_file:
            sequence_file.write(xml_sequence_request)
    else:
        with open(sequence_path, 'rb') as sequence_file:
            xml_sequence = ET.fromstring(gzip.decompress(sequence_file.read()))

    data = []
    for changeset in xml_sequence:
        changeset_data = {}
        for attribute, value in changeset.attrib.items():
            if attribute == "created_at":
                changeset_data['created_at'] = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        data.append(changeset_data)

    min_created_at = min(data, key=lambda x: x['created_at'])['created_at']
    max_created_at = max(data, key=lambda x: x['created_at'])['created_at']
    duration = max_created_at - min_created_at
    print('max created at : ' + str(max_created_at))
    print('min created at : ' + str(min_created_at))
    print("Duration : " + str(duration))
    return duration