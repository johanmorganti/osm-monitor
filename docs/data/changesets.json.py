import sys 
import requests
import xml.etree.ElementTree as ET
import gzip
import yaml, json


# Mapping XML into more readable collumns names

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

def process_sequence(sequence_number):

    sequence_number_adjusted = str(sequence_number).rjust(9, "0")
    url_sequence = "https://planet.osm.org/replication/changesets/" + sequence_number_adjusted[0:3] + "/" + sequence_number_adjusted[3:6] + "/" + sequence_number_adjusted[6:9] + ".osm.gz"
    request_sequence = requests.get(url_sequence, stream=True)
    xml_sequence = ET.fromstring(gzip.decompress(request_sequence.raw.read()))

    changesets_processed = []

    for changeset in xml_sequence:
        """
        Changeset overview :
            <changeset [...] attribute_key="attribute_value" [...]>
                [...]
                # list of elements, in OSM there is moslty tag elements, with k/v attributes for key/values
                <tag k="key" v="value"/>
                [...]
            </changeset>
        """
        """ 
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

        for attribute in changeset.attrib:
            if attribute in COLUMNS_MAPPING:
                changeset_to_add[COLUMNS_MAPPING[attribute]]=changeset.attrib[attribute]
                    
            else :
                print("Sequence number : " + sequence_number)
                print("Changeset " + changeset.attrib["id"])
                print("Changeset attribute not known : " + attribute)
                    
        for element in changeset:
            if 'tag' in element.tag:
                if 'k' in element.attrib:
                    changeset_to_add["tags"][element.attrib["k"]] = element.attrib["v"]
            elif 'discussion' in element.tag:
                ## TODO : implement
                continue
            else:
                print("Sequence number : " + str(sequence_number))
                print("Changeset : " + changeset.attrib["id"])
                print("Element of XML not being a <tag> nor <discussion> : " + element.tag)

        changesets_processed.append(changeset_to_add)

    return changesets_processed


list_changesets = []

# Process the last 1000 sequences
last_sequence = int(yaml.load(requests.get("https://planet.osm.org/replication/changesets/state.yaml", stream=True).raw.read(),Loader=yaml.FullLoader)["sequence"])
for seq_num in range(last_sequence - 2, last_sequence + 1):
    sys.stderr.write("processing " + str(seq_num))
    for changeset in process_sequence(seq_num):
        list_changesets.append(changeset)


sys.stdout.write(json.dumps(list_changesets))