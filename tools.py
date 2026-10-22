import json
import re
from typing import Dict, Any, List

PLACES_DB = {
    "london": [
        {"name": "Tower of London", "type": "Historic", "cost": 34, "rating": 4.7},
        {"name": "British Museum", "type": "Museum", "cost": 0, "rating": 4.8},
        {"name": "London Eye", "type": "Landmark", "cost": 38, "rating": 4.5},
        {"name": "The Savoy Luxury Stay", "type": "Hotel", "cost": 350, "rating": 4.9}
    ],
    "goa": [
        {"name": "Baga Beach", "type": "Beach", "cost": 0, "rating": 4.6},
        {"name": "Aguada Fort", "type": "Heritage", "cost": 25, "rating": 4.7},
        {"name": "Basilica of Bom Jesus", "type": "Cultural", "cost": 0, "rating": 4.8},
        {"name": "Beachside Boutique Resort", "type": "Hotel", "cost": 140, "rating": 4.9}
    ],
    "kyoto": [
        {"name": "Fushimi Inari Taisha", "type": "Shrine", "cost": 0, "rating": 4.8},
        {"name": "Kinkaku-ji (Golden Pavilion)", "type": "Temple", "cost": 500, "rating": 4.7},
        {"name": "Arashiyama Bamboo Grove", "type": "Nature", "cost": 0, "rating": 4.6},
        {"name": "Ryokan Traditional Stay", "type": "Hotel", "cost": 250, "rating": 4.9}
    ],
    "paris": [
        {"name": "Eiffel Tower", "type": "Landmark", "cost": 28, "rating": 4.7},
        {"name": "Louvre Museum", "type": "Museum", "cost": 22, "rating": 4.8},
        {"name": "Montmartre & Sacre-Coeur", "type": "Historic", "cost": 0, "rating": 4.6},
        {"name": "Boutique Hotel Le Marais", "type": "Hotel", "cost": 220, "rating": 4.9}
    ],
    "kolhapur": [
        {"name": "Mahalakshmi Temple", "type": "Temple", "cost": 0, "rating": 4.9},
        {"name": "New Palace Museum", "type": "Heritage", "cost": 50, "rating": 4.6},
        {"name": "Rankala Lake", "type": "Recreation", "cost": 0, "rating": 4.5},
        {"name": "Heritage Boutique Hotel", "type": "Hotel", "cost": 85, "rating": 4.8}
    ]
}

def parse_destination_fallback(query: str) -> str:
    patterns = [
        r"(?:trip|travel|visit|holiday|vacation)\s+(?:to|in)\s+([a-zA-Z\s]+?)(?:\s+from|\s+for|\s+with|$)",
        r"(?:to|in)\s+([a-zA-Z\s]+?)(?:\s+from|\s+for|\s+with|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            dest = match.group(1).strip()
            dest_words = [w for w in dest.split() if w.lower() not in ["a", "an", "the", "day", "days"]]
            if dest_words:
                return " ".join(dest_words).title()
    for known in PLACES_DB.keys():
        if known in query.lower():
            return known.title()
    return "Destination"

def search_places(destination: str, category: str = None) -> List[Dict[str, Any]]:
    key = destination.strip().lower()
    for city, places in PLACES_DB.items():
        if city == key or city in key:
            if category:
                return [p for p in places if p["type"].lower() == category.lower()]
            return places
    dest_name = destination.title()
    return [
        {"name": f"{dest_name} Central Heritage", "type": "Landmark", "cost": 30, "rating": 4.7},
        {"name": f"{dest_name} Botanical Garden", "type": "Nature", "cost": 15, "rating": 4.6},
        {"name": f"{dest_name} Old Town Walk", "type": "Cultural", "cost": 0, "rating": 4.8},
        {"name": f"{dest_name} Grand Boutique Hotel", "type": "Hotel", "cost": 160, "rating": 4.9}
    ]

def extract_trip_info(query: str, adapter = None) -> Dict[str, Any]:
    days_match = re.search(r"(\d+)\s*(?:day|days)", query, re.IGNORECASE)
    total_days = int(days_match.group(1)) if days_match else 3
    if total_days < 1:
        total_days = 1

    if adapter and hasattr(adapter, "generate_text"):
        prompt = (
            f"Analyze this travel request: \"{query}\". "
            f"Extract the destination and generate 3 to 5 realistic attractions and one hotel for a {total_days}-day trip. "
            f"Assign each item a 'day' integer from 1 to {total_days}. The hotel MUST have day: 1. "
            "Return ONLY raw JSON with keys: 'destination' (string), 'title' (clean string like '3-Day London Trip'), "
            "'places' (list of objects with 'name', 'type', 'cost' as integer number, 'rating' as float, 'day' as integer). "
            "Do not include markdown or backticks."
        )
        try:
            raw = adapter.generate_text(prompt)
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()
            data = json.loads(cleaned)
            if data.get("destination") and data.get("places") and len(data.get("places")) >= 3:
                places = data["places"]
                attraction_idx = 0
                for p in places:
                    if p.get("type", "").lower() == "hotel" or "hotel" in p.get("name", "").lower():
                        p["day"] = 1
                    else:
                        if not p.get("day") or p["day"] < 1 or p["day"] > total_days:
                            p["day"] = (attraction_idx % total_days) + 1
                        attraction_idx += 1
                return {
                    "destination": data["destination"].strip(),
                    "title": data.get("title", f"{total_days}-Day {data['destination'].title()} Trip").strip(),
                    "places": places
                }
        except Exception:
            pass

    destination = parse_destination_fallback(query)
    clean_title = f"{total_days}-Day {destination} Trip".strip()
    raw_places = search_places(destination)
    places = []
    attraction_idx = 0
    for p in raw_places:
        item = dict(p)
        if item.get("type", "").lower() == "hotel":
            item["day"] = 1
        else:
            item["day"] = (attraction_idx % total_days) + 1
            attraction_idx += 1
        places.append(item)

    return {
        "destination": destination,
        "title": clean_title,
        "places": places
    }

def get_frontend_tools_declaration() -> List[Dict[str, Any]]:
    return [
        {
            "name": "request_approval",
            "description": "Request human user approval for sensitive actions, expensive bookings, or plan finalization",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action name requiring approval"},
                    "details": {"type": "string", "description": "Explanation of the action and impact"},
                    "cost": {"type": "number", "description": "Associated cost amount in currency units"}
                },
                "required": ["action", "details"]
            }
        }
    ]
