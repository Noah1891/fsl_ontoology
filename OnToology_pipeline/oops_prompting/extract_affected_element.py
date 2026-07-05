import xml.etree.ElementTree as ET

# Namespace mapping
NS = {'oops': 'http://www.oeg-upm.net/oops'}


def get_pitfall_info(xml_path: str, pitfall_number: int) -> dict | None:
    """
    Parses the OOPS response XML and returns the name, description,
    and affected elements for a given pitfall number.

    :param xml_path: Path to the XML file
    :param pitfall_number: Number between 1 and 41 (e.g. 4 for "P04")
    :return: dict with 'code', 'name', 'description', 'importance',
             'num_affected_elements', 'affected_elements', or None if not found
    """
    code = f"P{pitfall_number:02d}"  # e.g. 4 -> "P04", 12 -> "P12"

    tree = ET.parse(xml_path)
    root = tree.getroot()

    for pitfall in root.findall('oops:Pitfall', NS):
        code_elem = pitfall.find('oops:Code', NS)
        if code_elem is not None and code_elem.text == code:
            name_elem = pitfall.find('oops:Name', NS)
            description_elem = pitfall.find('oops:Description', NS)
            importance_elem = pitfall.find('oops:Importance', NS)
            num_affected_elem = pitfall.find('oops:NumberAffectedElements', NS)

            affected_elements = [
                el.text
                for el in pitfall.findall('oops:Affects/oops:AffectedElement', NS)
            ]

            return {
                'code': code,
                'name': name_elem.text if name_elem is not None else None,
                'description': description_elem.text.strip() if description_elem is not None else None,
                'importance': importance_elem.text if importance_elem is not None else None,
                'num_affected_elements': num_affected_elem.text if num_affected_elem is not None else None,
                'affected_elements': affected_elements,
            }

    return None  # Pitfall code not found in the file


# Example usage
if __name__ == '__main__':
    result = get_pitfall_info('report/oops_report.xml', 8)
    if result:
        print(f"Code: {result['code']}")
        print(f"Name: {result['name']}")
        print(f"Description: {result['description']}")
        print(f"Affected elements ({result['num_affected_elements']}):")
        for element in result['affected_elements']:
            print(f"  - {element}")
    else:
        print("Pitfall not found.")