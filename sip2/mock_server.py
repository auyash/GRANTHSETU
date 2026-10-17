"""
High-Fidelity Mock SIP2 Server for Koha 26 at NFSU Library.
Emulates live Koha 26 SIP2 server behavior for offline testing, demos,
and failover resilience when external campus IP is unreachable.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sip2.protocol import (
    compute_checksum,
    format_message,
    get_sip_timestamp,
    parse_fields,
    SIP2ResponseParser,
)


class MockKohaSIP2:
    def __init__(self):
        # Sample NFSU Library Patrons
        self.patrons = {
            "NFSU2024001": {
                "name": "Aarav Sharma",
                "role": "M.Sc. Digital Forensics",
                "valid": True,
                "charged_items": [],
                "fines": "0.00",
            },
            "NFSU2024002": {
                "name": "Priya Nair",
                "role": "B.Tech. Cyber Security",
                "valid": True,
                "charged_items": ["BK-FOR-101"],
                "fines": "0.00",
            },
            "NFSU2024003": {
                "name": "Dr. Rajesh Verma",
                "role": "Faculty - Forensic Odontology",
                "valid": True,
                "charged_items": ["BK-CYB-201", "BK-DIG-301"],  # Limit of 2 books reached!
                "fines": "0.00",
            },
            "CARD_E3A59102": {
                "name": "Sneha Mukherjee",
                "role": "M.Sc. Forensic Ballistics",
                "valid": True,
                "charged_items": [],
                "fines": "0.00",
            },
            "FACIAL_VERIFIED_PATRON": {
                "name": "Kabir Das",
                "role": "Ph.D. Cyber Law",
                "valid": True,
                "charged_items": ["BK-BAL-401"],
                "fines": "0.00",
            },
            "BLOCKED_PATRON_01": {
                "name": "Suspended Student",
                "role": "Blocked Account",
                "valid": False,
                "charged_items": [],
                "fines": "500.00",
            }
        }

        # Sample NFSU Library Catalog Items
        self.items = {
            "BK-FOR-101": {
                "title": "Forensic Science in Criminal Investigation",
                "author": "Dr. B. R. Sharma",
                "barcode": "BK-FOR-101",
                "circ_status": "03",  # Available
                "eas_armed": True,    # Security tag armed
                "borrowed_by": "NFSU2024002",
                "due_date": (datetime.now() + timedelta(days=14)).strftime("%Y%m%d    235900"),
            },
            "BK-CYB-201": {
                "title": "Cyber Forensics & Incident Response",
                "author": "Gerard Johansen",
                "barcode": "BK-CYB-201",
                "circ_status": "03",
                "eas_armed": True,
                "borrowed_by": "NFSU2024003",
                "due_date": (datetime.now() + timedelta(days=7)).strftime("%Y%m%d    235900"),
            },
            "BK-DIG-301": {
                "title": "Digital Evidence & Computer Crime",
                "author": "Eoghan Casey",
                "barcode": "BK-DIG-301",
                "circ_status": "03",
                "eas_armed": True,
                "borrowed_by": "NFSU2024003",
                "due_date": (datetime.now() + timedelta(days=10)).strftime("%Y%m%d    235900"),
            },
            "BK-BAL-401": {
                "title": "Firearms, the Law, and Forensic Ballistics",
                "author": "Tom Warlow",
                "barcode": "BK-BAL-401",
                "circ_status": "03",
                "eas_armed": True,
                "borrowed_by": "FACIAL_VERIFIED_PATRON",
                "due_date": (datetime.now() + timedelta(days=12)).strftime("%Y%m%d    235900"),
            },
            "BK-TOX-501": {
                "title": "Clarke's Analytical Forensic Toxicology",
                "author": "Sue Jickells, Adam Negrusz",
                "barcode": "BK-TOX-501",
                "circ_status": "02",  # Available on shelf
                "eas_armed": True,
                "borrowed_by": None,
                "due_date": None,
            },
            "BK-DNA-601": {
                "title": "Advanced DNA Forensic Analysis & Profiling",
                "author": "John M. Butler",
                "barcode": "BK-DNA-601",
                "circ_status": "02",
                "eas_armed": True,
                "borrowed_by": None,
                "due_date": None,
            },
        }

    def handle_raw_message(self, raw_msg: str) -> str:
        """Processes an incoming raw SIP2 message string and returns the raw response."""
        msg = raw_msg.strip("\r\n")
        if not msg:
            return ""

        msg_type = msg[:2]
        ts = get_sip_timestamp()

        # Extract incoming sequence number AY if present
        all_fields = parse_fields(msg[2:])
        ay_in = all_fields.get("AY")
        seq_out = int(ay_in) if ay_in and ay_in.isdigit() else None

        # 93 -> 94 (Login)
        if msg_type == "93":
            fields = parse_fields(msg[4:])
            username = fields.get("CN", "")
            password = fields.get("CO", "")
            # Valid accounts in simulation: accounts starting with 'rfid' (e.g., rfid, rfid1, rfid2)
            ok = "1" if username.startswith("rfid") else "0"
            return format_message(f"94{ok}", seq_num=seq_out)

        # 63 -> 64 (Patron Information)
        elif msg_type == "63":
            fields = parse_fields(msg[33:])
            patron_id = fields.get("AA", "").strip()
            
            # Auto-enroll unrecognized patron if needed, or query existing
            patron = self.patrons.get(patron_id)
            if not patron:
                # Default generic patron profile
                patron = {
                    "name": f"Patron ({patron_id})",
                    "role": "NFSU Student",
                    "valid": True,
                    "charged_items": [],
                    "fines": "0.00",
                }
                self.patrons[patron_id] = patron

            charged_count = len(patron["charged_items"])
            charged_items_str = "".join([f"|AS{item}" for item in patron["charged_items"]])
            
            # Format: 64<status 14><lang 3><date 18><hold 4><overdue 4><charged 4>
            # <fine 4><recall 4><unavail 4>AO|AA|AE|BL|BK|...
            status = "              "
            lang = "001"
            body = (
                f"64{status}{lang}{ts}"
                f"00000000{charged_count:04d}000000000000"
                f"AONFSU|AA{patron_id}|AE{patron['name']}|BL{'Y' if patron['valid'] else 'N'}|"
                f"BK{charged_count}{charged_items_str}|"
            )
            return format_message(body, seq_num=seq_out)

        # 17 -> 18 (Item Information)
        elif msg_type == "17":
            fields = parse_fields(msg[20:])
            item_id = fields.get("AB", "").strip()
            item = self.items.get(item_id)
            if not item:
                item = {
                    "title": f"Library Material {item_id}",
                    "author": "NFSU Library Resource",
                    "barcode": item_id,
                    "circ_status": "02",
                    "eas_armed": True,
                    "borrowed_by": None,
                    "due_date": None,
                }
                self.items[item_id] = item

            circ_status = "03" if item["borrowed_by"] else "02"
            body = (
                f"18{circ_status}0100{ts}"
                f"AB{item_id}|AJ{item['title']}|CF{item['author']}|"
                f"AQNFSU Central Library|APNFSU|AONFSU|"
            )
            return format_message(body, seq_num=seq_out)

        # 11 -> 12 (Checkout)
        elif msg_type == "11":
            fields = parse_fields(msg[40:])
            patron_id = fields.get("AA", "").strip()
            item_id = fields.get("AB", "").strip()

            patron = self.patrons.get(patron_id, {"charged_items": [], "name": "Patron"})
            item = self.items.get(item_id, {"title": f"Book {item_id}", "author": "NFSU Author"})

            # Strict 2-book limit rule:
            if len(patron["charged_items"]) >= 2:
                # Checkout DENIED - Limit exceeded!
                body = (
                    f"120NNN{ts}"
                    f"AONFSU|AA{patron_id}|AB{item_id}|AJ{item.get('title')}|"
                    f"AFBorrowing limit reached (Maximum 2 books allowed). Please return a book first.|"
                )
                return format_message(body, seq_num=seq_out)

            # Checkout SUCCESS
            if item_id not in patron["charged_items"]:
                patron["charged_items"].append(item_id)
            item["borrowed_by"] = patron_id
            item["eas_armed"] = False  # DISARMED
            due_dt = (datetime.now() + timedelta(days=14)).strftime("%Y%m%d    235900")
            item["due_date"] = due_dt

            # desensitize='Y' indicates EAS tag is disarmed
            body = (
                f"121NYY{ts}"
                f"AONFSU|AA{patron_id}|AB{item_id}|AJ{item.get('title')}|"
                f"AH{due_dt}|AFCheckout successful. RFID security disarmed.|"
            )
            return format_message(body, seq_num=seq_out)

        # 09 -> 10 (Checkin)
        elif msg_type == "09":
            fields = parse_fields(msg[39:])
            item_id = fields.get("AB", "").strip()
            item = self.items.get(item_id, {"title": f"Book {item_id}"})
            
            # Remove from whoever borrowed it
            for pid, pdata in self.patrons.items():
                if item_id in pdata["charged_items"]:
                    pdata["charged_items"].remove(item_id)
                    break

            item["borrowed_by"] = None
            item["eas_armed"] = True  # RE-ARMED
            item["due_date"] = None

            # resensitize='Y' indicates EAS tag is re-armed
            body = (
                f"101YYN{ts}"
                f"AONFSU|AB{item_id}|AJ{item.get('title')}|AQNFSU Central Library|"
                f"AFItem returned successfully. RFID security re-armed.|"
            )
            return format_message(body, seq_num=seq_out)

        # 29 -> 30 (Renew)
        elif msg_type == "29":
            fields = parse_fields(msg[40:])
            patron_id = fields.get("AA", "").strip()
            item_id = fields.get("AB", "").strip()
            item = self.items.get(item_id, {"title": f"Book {item_id}"})

            new_due = (datetime.now() + timedelta(days=14)).strftime("%Y%m%d    235900")
            item["due_date"] = new_due

            body = (
                f"301YYY{ts}"
                f"AONFSU|AA{patron_id}|AB{item_id}|AJ{item.get('title')}|"
                f"AH{new_due}|AFRenewal successful. Due date extended.|"
            )
            return format_message(body, seq_num=seq_out)

        # 35 -> 36 (End Session)
        elif msg_type == "35":
            fields = parse_fields(msg[20:])
            patron_id = fields.get("AA", "").strip()
            body = f"36Y{ts}AONFSU|AA{patron_id}|AFSession ended.|"
            return format_message(body, seq_num=seq_out)

        return format_message(f"96{ts}", seq_num=seq_out)
