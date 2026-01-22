"""
Staff Assistant Service for AppointMint

This service provides AI-powered assistance for restaurant staff to manage reservations.
It handles queries about reservations, status updates, and provides quick insights.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from app.models import db, Reservation, Restaurant, Table
from app.services.datetime_utils import get_current_datetime


class StaffAssistantState(BaseModel):
    """State for staff assistant conversation"""
    restaurant_id: int
    timezone: str = "UTC"
    last_query_type: Optional[str] = None
    last_results: Optional[List[int]] = None  # List of reservation IDs from last query


class StaffAssistant:
    """AI-powered assistant for restaurant staff to manage reservations"""
    
    def __init__(self, restaurant_id: int):
        self.restaurant_id = restaurant_id
        self.restaurant = Restaurant.query.get(restaurant_id)
        self.timezone = self.restaurant.timezone if self.restaurant else "UTC"
        self._state = StaffAssistantState(
            restaurant_id=restaurant_id,
            timezone=self.timezone
        )
    
    def _get_current_datetime_info(self) -> Dict[str, Any]:
        """Get current datetime information in restaurant's timezone"""
        return get_current_datetime(self.timezone)
    
    def _format_reservation(self, res: Reservation, include_details: bool = True) -> Dict[str, Any]:
        """Format a reservation for display"""
        data = {
            "id": res.id,
            "customer_name": res.customer_name,
            "party_size": res.party_size,
            "time": res.reservation_time.strftime("%I:%M %p") if res.reservation_time else "N/A",
            "status": res.status,
            "table": res.table.name if res.table else f"Table {res.table.table_number}" if res.table else "Unassigned"
        }
        if include_details:
            data["phone"] = res.customer_phone
            data["date"] = res.reservation_date.strftime("%B %d, %Y") if res.reservation_date else "N/A"
            data["special_requests"] = res.special_requests
        return data
    
    def get_todays_reservations(self) -> Dict[str, Any]:
        """Get all reservations for today"""
        datetime_info = self._get_current_datetime_info()
        today = datetime.strptime(datetime_info['current_date'], "%Y-%m-%d").date()
        
        reservations = Reservation.query.filter(
            Reservation.restaurant_id == self.restaurant_id,
            Reservation.reservation_date == today
        ).order_by(Reservation.reservation_time).all()
        
        self._state.last_query_type = "todays_reservations"
        self._state.last_results = [r.id for r in reservations]
        
        return {
            "type": "reservations_list",
            "title": f"Today's Reservations ({datetime_info['formatted_date']})",
            "count": len(reservations),
            "reservations": [self._format_reservation(r, include_details=False) for r in reservations]
        }
    
    def get_upcoming_reservations(self, hours: int = 2) -> Dict[str, Any]:
        """Get reservations in the next N hours"""
        datetime_info = self._get_current_datetime_info()
        today = datetime.strptime(datetime_info['current_date'], "%Y-%m-%d").date()
        current_time = datetime.strptime(datetime_info['current_time'], "%H:%M").time()
        
        # Calculate end time
        current_datetime = datetime.combine(today, current_time)
        end_datetime = current_datetime + timedelta(hours=hours)
        
        reservations = Reservation.query.filter(
            Reservation.restaurant_id == self.restaurant_id,
            Reservation.reservation_date == today,
            Reservation.reservation_time >= current_time,
            Reservation.reservation_time <= end_datetime.time(),
            Reservation.status.in_(['confirmed', 'pending'])
        ).order_by(Reservation.reservation_time).all()
        
        self._state.last_query_type = "upcoming_reservations"
        self._state.last_results = [r.id for r in reservations]
        
        return {
            "type": "reservations_list",
            "title": f"Upcoming Reservations (Next {hours} Hours)",
            "count": len(reservations),
            "reservations": [self._format_reservation(r, include_details=False) for r in reservations]
        }
    
    def get_todays_stats(self) -> Dict[str, Any]:
        """Get statistics for today"""
        datetime_info = self._get_current_datetime_info()
        today = datetime.strptime(datetime_info['current_date'], "%Y-%m-%d").date()
        
        all_reservations = Reservation.query.filter(
            Reservation.restaurant_id == self.restaurant_id,
            Reservation.reservation_date == today
        ).all()
        
        total = len(all_reservations)
        confirmed = len([r for r in all_reservations if r.status == 'confirmed'])
        pending = len([r for r in all_reservations if r.status == 'pending'])
        completed = len([r for r in all_reservations if r.status == 'completed'])
        cancelled = len([r for r in all_reservations if r.status == 'cancelled'])
        no_show = len([r for r in all_reservations if r.status == 'no_show'])
        total_guests = sum(r.party_size for r in all_reservations if r.status in ['confirmed', 'pending', 'completed'])
        
        return {
            "type": "stats",
            "title": f"Today's Statistics ({datetime_info['formatted_date']})",
            "stats": {
                "total_reservations": total,
                "confirmed": confirmed,
                "pending": pending,
                "completed": completed,
                "cancelled": cancelled,
                "no_show": no_show,
                "total_guests": total_guests
            }
        }
    
    def get_pending_bookings(self) -> Dict[str, Any]:
        """Get all pending reservations"""
        datetime_info = self._get_current_datetime_info()
        today = datetime.strptime(datetime_info['current_date'], "%Y-%m-%d").date()
        
        reservations = Reservation.query.filter(
            Reservation.restaurant_id == self.restaurant_id,
            Reservation.reservation_date >= today,
            Reservation.status == 'pending'
        ).order_by(Reservation.reservation_date, Reservation.reservation_time).all()
        
        self._state.last_query_type = "pending_bookings"
        self._state.last_results = [r.id for r in reservations]
        
        return {
            "type": "reservations_list",
            "title": "Pending Bookings (Awaiting Confirmation)",
            "count": len(reservations),
            "reservations": [self._format_reservation(r) for r in reservations]
        }
    
    def get_confirmed_bookings(self) -> Dict[str, Any]:
        """Get all confirmed reservations for today and future"""
        datetime_info = self._get_current_datetime_info()
        today = datetime.strptime(datetime_info['current_date'], "%Y-%m-%d").date()
        
        reservations = Reservation.query.filter(
            Reservation.restaurant_id == self.restaurant_id,
            Reservation.reservation_date >= today,
            Reservation.status == 'confirmed'
        ).order_by(Reservation.reservation_date, Reservation.reservation_time).all()
        
        self._state.last_query_type = "confirmed_bookings"
        self._state.last_results = [r.id for r in reservations]
        
        return {
            "type": "reservations_list",
            "title": "Confirmed Bookings",
            "count": len(reservations),
            "reservations": [self._format_reservation(r) for r in reservations]
        }
    
    def get_reservation_details(self, reservation_id: int) -> Dict[str, Any]:
        """Get detailed information about a specific reservation"""
        reservation = Reservation.query.filter(
            Reservation.id == reservation_id,
            Reservation.restaurant_id == self.restaurant_id
        ).first()
        
        if not reservation:
            return {
                "type": "error",
                "message": f"Reservation #{reservation_id} not found."
            }
        
        return {
            "type": "reservation_detail",
            "reservation": {
                "id": reservation.id,
                "customer_name": reservation.customer_name,
                "customer_phone": reservation.customer_phone,
                "customer_email": reservation.customer_email,
                "party_size": reservation.party_size,
                "date": reservation.reservation_date.strftime("%A, %B %d, %Y") if reservation.reservation_date else "N/A",
                "time": reservation.reservation_time.strftime("%I:%M %p") if reservation.reservation_time else "N/A",
                "status": reservation.status,
                "table": reservation.table.name if reservation.table else f"Table {reservation.table.table_number}" if reservation.table else "Unassigned",
                "special_requests": reservation.special_requests,
                "source": reservation.source,
                "created_at": reservation.created_at.strftime("%b %d, %Y at %I:%M %p") if reservation.created_at else "N/A"
            }
        }
    
    def update_reservation_status(self, reservation_id: int, new_status: str) -> Dict[str, Any]:
        """Update the status of a reservation"""
        valid_statuses = ['pending', 'confirmed', 'completed', 'cancelled', 'no_show', 'arrived', 'seated']
        
        if new_status.lower() not in valid_statuses:
            return {
                "type": "error",
                "message": f"Invalid status '{new_status}'. Valid statuses are: {', '.join(valid_statuses)}"
            }
        
        reservation = Reservation.query.filter(
            Reservation.id == reservation_id,
            Reservation.restaurant_id == self.restaurant_id
        ).first()
        
        if not reservation:
            return {
                "type": "error",
                "message": f"Reservation #{reservation_id} not found."
            }
        
        old_status = reservation.status
        reservation.status = new_status.lower()
        db.session.commit()
        
        return {
            "type": "success",
            "message": f"Reservation #{reservation_id} status updated from '{old_status}' to '{new_status}'.",
            "reservation": self._format_reservation(reservation)
        }
    
    def update_reservation_guests(self, reservation_id: int, new_guest_count: int) -> Dict[str, Any]:
        """Update the number of guests for a reservation"""
        if new_guest_count < 1 or new_guest_count > 50:
            return {
                "type": "error",
                "message": "Guest count must be between 1 and 50."
            }
        
        reservation = Reservation.query.filter(
            Reservation.id == reservation_id,
            Reservation.restaurant_id == self.restaurant_id
        ).first()
        
        if not reservation:
            return {
                "type": "error",
                "message": f"Reservation #{reservation_id} not found."
            }
        
        old_count = reservation.party_size
        reservation.party_size = new_guest_count
        db.session.commit()
        
        return {
            "type": "success",
            "message": f"Reservation #{reservation_id} has been updated to {new_guest_count} guests (was {old_count}).",
            "reservation": self._format_reservation(reservation)
        }
    
    def search_reservations(self, query: str) -> Dict[str, Any]:
        """Search reservations by customer name or phone"""
        reservations = Reservation.query.filter(
            Reservation.restaurant_id == self.restaurant_id,
            db.or_(
                Reservation.customer_name.ilike(f"%{query}%"),
                Reservation.customer_phone.ilike(f"%{query}%")
            )
        ).order_by(Reservation.reservation_date.desc()).limit(20).all()
        
        self._state.last_query_type = "search"
        self._state.last_results = [r.id for r in reservations]
        
        return {
            "type": "reservations_list",
            "title": f"Search Results for '{query}'",
            "count": len(reservations),
            "reservations": [self._format_reservation(r) for r in reservations]
        }
    
    def chat_sync(self, message: str, conversation_history: Optional[List] = None) -> str:
        """
        Process a staff message and return a response.
        Uses pattern matching for common queries and OpenAI for complex requests.
        """
        message_lower = message.lower().strip()
        
        # Quick command patterns
        if any(phrase in message_lower for phrase in ["today's reservations", "todays reservations", "today reservations", "reservations today"]):
            result = self.get_todays_reservations()
            return json.dumps({"type": "data", "data": result})
        
        if any(phrase in message_lower for phrase in ["upcoming", "next 2 hours", "coming up", "soon"]):
            hours = 2
            if "hour" in message_lower:
                # Try to extract hours
                import re
                match = re.search(r'(\d+)\s*hour', message_lower)
                if match:
                    hours = int(match.group(1))
            result = self.get_upcoming_reservations(hours)
            return json.dumps({"type": "data", "data": result})
        
        if any(phrase in message_lower for phrase in ["stats", "statistics", "summary", "overview"]):
            result = self.get_todays_stats()
            return json.dumps({"type": "data", "data": result})
        
        if any(phrase in message_lower for phrase in ["pending", "awaiting", "unconfirmed"]):
            result = self.get_pending_bookings()
            return json.dumps({"type": "data", "data": result})
        
        if any(phrase in message_lower for phrase in ["confirmed", "confirmed bookings"]):
            result = self.get_confirmed_bookings()
            return json.dumps({"type": "data", "data": result})
        
        # Status update patterns
        import re
        
        # Pattern: "update id X to status Y" or "mark id X as Y"
        status_pattern = r'(?:update|mark|set|change)\s+(?:id\s*)?#?(\d+)\s+(?:to|as|status)\s+(\w+)'
        status_match = re.search(status_pattern, message_lower)
        if status_match:
            res_id = int(status_match.group(1))
            new_status = status_match.group(2)
            result = self.update_reservation_status(res_id, new_status)
            return json.dumps({"type": "data", "data": result})
        
        # Pattern: "update id X to Y guests" or "change id X to Y guests"
        guests_pattern = r'(?:update|change|set)\s+(?:id\s*)?#?(\d+)\s+(?:to\s+)?(\d+)\s+guests?'
        guests_match = re.search(guests_pattern, message_lower)
        if guests_match:
            res_id = int(guests_match.group(1))
            guest_count = int(guests_match.group(2))
            result = self.update_reservation_guests(res_id, guest_count)
            return json.dumps({"type": "data", "data": result})
        
        # Pattern: "reservation #X" or "details for X" or "show X"
        detail_pattern = r'(?:reservation|details?|show|view|get)\s*#?(\d+)'
        detail_match = re.search(detail_pattern, message_lower)
        if detail_match:
            res_id = int(detail_match.group(1))
            result = self.get_reservation_details(res_id)
            return json.dumps({"type": "data", "data": result})
        
        # Search pattern
        search_pattern = r'(?:search|find|look\s+for)\s+(.+)'
        search_match = re.search(search_pattern, message_lower)
        if search_match:
            query = search_match.group(1).strip()
            result = self.search_reservations(query)
            return json.dumps({"type": "data", "data": result})
        
        # If no pattern matched, try to use OpenAI for understanding
        try:
            from openai import OpenAI
            client = OpenAI()
            
            system_prompt = f"""You are a helpful staff assistant for {self.restaurant.name if self.restaurant else 'the restaurant'}.
You help staff manage reservations. Based on the user's message, determine what they want to do and respond with a helpful message.

Available actions you can suggest:
- View today's reservations
- View upcoming reservations (next 2 hours)
- View today's stats
- View pending bookings
- View confirmed bookings
- Update reservation status (e.g., "update id 5 to confirmed")
- Update guest count (e.g., "update id 5 to 4 guests")
- View reservation details (e.g., "show reservation 5")
- Search reservations (e.g., "search John Smith")

Current time: {self._get_current_datetime_info()['formatted_time']}
Current date: {self._get_current_datetime_info()['formatted_date']}

Keep responses brief and helpful. If you're not sure what the user wants, ask for clarification."""

            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                max_tokens=300
            )
            
            return json.dumps({
                "type": "text",
                "text": response.choices[0].message.content
            })
            
        except Exception as e:
            # Fallback response
            return json.dumps({
                "type": "text",
                "text": f"I can help you with:\n• Today's reservations\n• Upcoming reservations\n• Today's stats\n• Pending bookings\n• Confirmed bookings\n• Update status (e.g., 'update id 5 to confirmed')\n• Update guests (e.g., 'update id 5 to 4 guests')\n• View details (e.g., 'show reservation 5')\n• Search (e.g., 'search John')\n\nWhat would you like to do?"
            })
