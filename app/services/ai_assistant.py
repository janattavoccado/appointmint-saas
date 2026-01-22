"""
AI Reservation Assistant Service with Pydantic State Management
Supports both text and voice interactions for restaurant table reservations.

This module uses Pydantic models for structured state management and
a conversational flow for collecting reservation details.

Reservation Flow:
1. Detect reservation intent
2. Collect date (multiple formats supported)
3. Collect time (12h and 24h formats supported)
4. Collect number of guests (>8 guests = handover to human staff)
5. Confirm name and phone (extracted from incoming message)
6. Collect special requests
7. Final confirmation and database storage
"""

import os
import re
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from openai import OpenAI

from app.models import db, Restaurant, Table, Reservation, AIConversation, Tenant


# =============================================================================
# PYDANTIC MODELS FOR STRUCTURED DATA
# =============================================================================

class BookingState(str, Enum):
    """Enum for booking conversation states"""
    INITIAL = 'initial'
    AWAITING_DATE = 'awaiting_date'
    AWAITING_TIME = 'awaiting_time'
    AWAITING_GUESTS = 'awaiting_guests'
    AWAITING_NAME_CONFIRMATION = 'awaiting_name_confirmation'
    AWAITING_SPECIAL_REQUESTS = 'awaiting_special_requests'
    AWAITING_FINAL_CONFIRMATION = 'awaiting_final_confirmation'
    HANDOVER_TO_HUMAN = 'handover_to_human'
    COMPLETED = 'completed'


class CustomerInfo(BaseModel):
    """Pydantic model for customer information extracted from incoming message"""
    name: Optional[str] = Field(None, description="Customer name")
    phone: Optional[str] = Field(None, description="Customer phone number")
    
    @field_validator('phone', mode='before')
    @classmethod
    def normalize_phone(cls, v):
        if v:
            # Remove common formatting but keep the + for international
            cleaned = re.sub(r'[\s\-\(\)]', '', str(v))
            return cleaned
        return v


class BookingDetails(BaseModel):
    """Pydantic model for complete booking details"""
    # Date information
    date: Optional[str] = Field(None, description="Reservation date in YYYY-MM-DD format")
    date_display: Optional[str] = Field(None, description="Human-readable date display")
    date_raw_input: Optional[str] = Field(None, description="Original user input for date")
    
    # Time information
    time: Optional[str] = Field(None, description="Reservation time in HH:MM format (24h)")
    time_display: Optional[str] = Field(None, description="Human-readable time display (12-hour)")
    time_raw_input: Optional[str] = Field(None, description="Original user input for time")
    
    # Guest information
    guests: Optional[int] = Field(None, description="Number of guests", ge=1, le=50)
    
    # Customer information
    customer_name: Optional[str] = Field(None, description="Customer name")
    customer_phone: Optional[str] = Field(None, description="Customer phone number")
    customer_email: Optional[str] = Field(None, description="Customer email (optional)")
    
    # Additional details
    special_requests: Optional[str] = Field(None, description="Special requests or notes")
    
    # Metadata
    requires_human_handover: bool = Field(False, description="Whether this booking requires human staff")
    handover_reason: Optional[str] = Field(None, description="Reason for human handover")


class ConversationState(BaseModel):
    """Pydantic model for conversation state - persisted between requests"""
    state: BookingState = Field(default=BookingState.INITIAL, description="Current booking state")
    booking_details: BookingDetails = Field(default_factory=BookingDetails, description="Collected booking details")
    restaurant_id: int = Field(..., description="Restaurant ID")
    timezone: str = Field(default='UTC', description="Restaurant timezone")
    
    # Customer info from incoming message (Chatwoot provides this)
    incoming_customer_name: Optional[str] = Field(None, description="Name from incoming message")
    incoming_customer_phone: Optional[str] = Field(None, description="Phone from incoming message")


class ButtonOption(BaseModel):
    """Pydantic model for a button option"""
    value: str = Field(..., description="Value sent when button is clicked")
    display: str = Field(..., description="Display text for the button")


class AssistantResponse(BaseModel):
    """Pydantic model for assistant response"""
    text: str = Field(..., description="Response text message")
    buttons: Optional[List[ButtonOption]] = Field(None, description="Optional buttons to display")
    button_type: Optional[str] = Field(None, description="Type of buttons: date, time, guests, confirm")
    conversation_state: ConversationState = Field(..., description="Current conversation state to persist")


# =============================================================================
# DATE AND TIME PARSING UTILITIES
# =============================================================================

class DateTimeParser:
    """Utility class for parsing various date and time formats"""
    
    WEEKDAYS = {
        'monday': 0, 'mon': 0,
        'tuesday': 1, 'tue': 1, 'tues': 1,
        'wednesday': 2, 'wed': 2,
        'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
        'friday': 4, 'fri': 4,
        'saturday': 5, 'sat': 5,
        'sunday': 6, 'sun': 6
    }
    
    MONTHS = {
        'january': 1, 'jan': 1,
        'february': 2, 'feb': 2,
        'march': 3, 'mar': 3,
        'april': 4, 'apr': 4,
        'may': 5,
        'june': 6, 'jun': 6,
        'july': 7, 'jul': 7,
        'august': 8, 'aug': 8,
        'september': 9, 'sep': 9, 'sept': 9,
        'october': 10, 'oct': 10,
        'november': 11, 'nov': 11,
        'december': 12, 'dec': 12
    }
    
    @classmethod
    def parse_date(cls, text: str, timezone: str = 'UTC') -> Optional[Tuple[datetime, str]]:
        """
        Parse date from various formats.
        
        Supported formats:
        - YYYY-MM-DD (2026-01-22)
        - MM/DD/YYYY (01/22/2026)
        - DD/MM/YYYY (22/01/2026) - detected by context
        - Weekday (Monday, Tuesday, etc.)
        - Weekday + day (Monday 22, Tuesday the 15th)
        - Relative (today, tomorrow, day after tomorrow)
        - Month + day (January 22, Jan 22nd)
        
        Returns:
            Tuple of (datetime object, human-readable display) or None
        """
        text = text.lower().strip()
        today = datetime.now()
        
        # Try "today"
        if text in ['today', 'hoy', 'aujourd\'hui', 'heute', 'idag']:
            return (today, f"Today ({today.strftime('%A, %B %d')})")
        
        # Try "tomorrow"
        if text in ['tomorrow', 'mañana', 'demain', 'morgen', 'imorgon']:
            tomorrow = today + timedelta(days=1)
            return (tomorrow, f"Tomorrow ({tomorrow.strftime('%A, %B %d')})")
        
        # Try "day after tomorrow"
        if 'day after tomorrow' in text or 'pasado mañana' in text:
            day_after = today + timedelta(days=2)
            return (day_after, day_after.strftime('%A, %B %d'))
        
        # Try YYYY-MM-DD format
        match = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', text)
        if match:
            try:
                year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
                date = datetime(year, month, day)
                return (date, date.strftime('%A, %B %d, %Y'))
            except ValueError:
                pass
        
        # Try MM/DD/YYYY format
        match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', text)
        if match:
            try:
                month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                date = datetime(year, month, day)
                return (date, date.strftime('%A, %B %d, %Y'))
            except ValueError:
                pass
        
        # Try DD/MM/YYYY format (European)
        match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', text)
        if match:
            try:
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                date = datetime(year, month, day)
                return (date, date.strftime('%A, %B %d, %Y'))
            except ValueError:
                pass
        
        # Try weekday only (next occurrence)
        for weekday_name, weekday_num in cls.WEEKDAYS.items():
            if text == weekday_name or text.startswith(weekday_name + ' '):
                days_ahead = weekday_num - today.weekday()
                if days_ahead <= 0:  # Target day already happened this week
                    days_ahead += 7
                target_date = today + timedelta(days=days_ahead)
                return (target_date, target_date.strftime('%A, %B %d'))
        
        # Try "next [weekday]"
        match = re.match(r'^next\s+(\w+)$', text)
        if match:
            weekday_text = match.group(1).lower()
            if weekday_text in cls.WEEKDAYS:
                weekday_num = cls.WEEKDAYS[weekday_text]
                days_ahead = weekday_num - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                target_date = today + timedelta(days=days_ahead)
                return (target_date, target_date.strftime('%A, %B %d'))
        
        # Try "this [weekday]"
        match = re.match(r'^this\s+(\w+)$', text)
        if match:
            weekday_text = match.group(1).lower()
            if weekday_text in cls.WEEKDAYS:
                weekday_num = cls.WEEKDAYS[weekday_text]
                days_ahead = weekday_num - today.weekday()
                if days_ahead < 0:
                    days_ahead += 7
                target_date = today + timedelta(days=days_ahead)
                return (target_date, target_date.strftime('%A, %B %d'))
        
        # Try month + day (January 22, Jan 22nd, 22nd of January)
        for month_name, month_num in cls.MONTHS.items():
            # "January 22" or "Jan 22nd"
            match = re.search(rf'{month_name}\s+(\d{{1,2}})(?:st|nd|rd|th)?', text)
            if match:
                try:
                    day = int(match.group(1))
                    year = today.year
                    date = datetime(year, month_num, day)
                    # If date is in the past, use next year
                    if date < today:
                        date = datetime(year + 1, month_num, day)
                    return (date, date.strftime('%A, %B %d, %Y'))
                except ValueError:
                    pass
            
            # "22nd of January"
            match = re.search(rf'(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{month_name}', text)
            if match:
                try:
                    day = int(match.group(1))
                    year = today.year
                    date = datetime(year, month_num, day)
                    if date < today:
                        date = datetime(year + 1, month_num, day)
                    return (date, date.strftime('%A, %B %d, %Y'))
                except ValueError:
                    pass
        
        # Try "in X days"
        match = re.match(r'^in\s+(\d+)\s+days?$', text)
        if match:
            days = int(match.group(1))
            target_date = today + timedelta(days=days)
            return (target_date, target_date.strftime('%A, %B %d'))
        
        return None
    
    @classmethod
    def parse_time(cls, text: str) -> Optional[Tuple[int, int, str]]:
        """
        Parse time from various formats.
        
        Supported formats:
        - 24-hour: 14:30, 1430, 14.30
        - 12-hour: 2:30 PM, 2:30pm, 2pm, 2 pm
        - Natural: noon, midnight, evening, etc.
        
        Returns:
            Tuple of (hour, minute, display_string) or None
        """
        text = text.lower().strip()
        
        # Handle natural language times
        natural_times = {
            'noon': (12, 0),
            'midday': (12, 0),
            'midnight': (0, 0),
            'morning': (10, 0),
            'lunch': (12, 0),
            'lunchtime': (12, 0),
            'afternoon': (14, 0),
            'evening': (18, 0),
            'dinner': (19, 0),
            'dinnertime': (19, 0),
            'night': (20, 0),
        }
        
        if text in natural_times:
            hour, minute = natural_times[text]
            return cls._format_time_result(hour, minute)
        
        # Try 12-hour format with AM/PM: "2:30 PM", "2:30pm", "2 pm", "2pm"
        match = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)$', text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)) if match.group(2) else 0
            period = match.group(3).replace('.', '')
            
            if period in ['pm', 'pm'] and hour != 12:
                hour += 12
            elif period in ['am', 'am'] and hour == 12:
                hour = 0
            
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return cls._format_time_result(hour, minute)
        
        # Try 24-hour format: "14:30", "14.30", "1430"
        match = re.match(r'^(\d{1,2})[:.](\d{2})$', text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return cls._format_time_result(hour, minute)
        
        # Try 4-digit format: "1430"
        match = re.match(r'^(\d{2})(\d{2})$', text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return cls._format_time_result(hour, minute)
        
        # Try simple hour: "7", "19"
        match = re.match(r'^(\d{1,2})$', text)
        if match:
            hour = int(match.group(1))
            if 0 <= hour <= 23:
                return cls._format_time_result(hour, 0)
        
        # Try "at X" format
        match = re.match(r'^at\s+(.+)$', text)
        if match:
            return cls.parse_time(match.group(1))
        
        # Try "X o'clock"
        match = re.match(r'^(\d{1,2})\s*o\'?clock(?:\s*(am|pm))?$', text)
        if match:
            hour = int(match.group(1))
            period = match.group(2)
            if period == 'pm' and hour != 12:
                hour += 12
            elif period == 'am' and hour == 12:
                hour = 0
            if 0 <= hour <= 23:
                return cls._format_time_result(hour, 0)
        
        return None
    
    @classmethod
    def _format_time_result(cls, hour: int, minute: int) -> Tuple[int, int, str]:
        """Format time result with display string"""
        period = 'AM' if hour < 12 else 'PM'
        display_hour = hour % 12 or 12
        if minute:
            display = f"{display_hour}:{minute:02d} {period}"
        else:
            display = f"{display_hour} {period}"
        return (hour, minute, display)


# =============================================================================
# RESERVATION ASSISTANT CLASS
# =============================================================================

class ReservationAssistant:
    """AI-powered reservation assistant with Pydantic state management"""
    
    MAX_GUESTS_WITHOUT_HANDOVER = 8
    
    def __init__(self, restaurant_id: int, app=None, customer_info: Optional[CustomerInfo] = None):
        """
        Initialize the reservation assistant.
        
        Args:
            restaurant_id: The restaurant ID
            app: Flask app instance for database context
            customer_info: Optional customer info from incoming message (name, phone)
        """
        self.restaurant_id = restaurant_id
        self.app = app
        self._restaurant_info = None
        self._timezone = 'UTC'
        self._client = OpenAI()
        self._customer_info = customer_info
    
    def _get_restaurant_info(self) -> Dict[str, Any]:
        """Get restaurant information from database"""
        if self._restaurant_info:
            return self._restaurant_info
        
        with self.app.app_context():
            restaurant = Restaurant.query.get(self.restaurant_id)
            if not restaurant:
                return {}
            
            self._timezone = restaurant.timezone or 'UTC'
                
            self._restaurant_info = {
                'id': restaurant.id,
                'name': restaurant.name,
                'address': restaurant.address,
                'city': restaurant.city,
                'phone': restaurant.phone,
                'timezone': self._timezone,
                'cuisine_type': getattr(restaurant, 'cuisine_type', None) or 'Various',
                'description': getattr(restaurant, 'description', None) or '',
                'knowledge_base': getattr(restaurant, 'knowledge_base', None) or '',
                'tables': [
                    {
                        'id': t.id,
                        'name': t.name,
                        'capacity': t.capacity,
                        'location': t.location
                    }
                    for t in restaurant.tables if t.is_active
                ]
            }
        return self._restaurant_info
    
    def _get_next_7_dates(self) -> List[ButtonOption]:
        """Get the next 7 available dates starting from today"""
        today = datetime.now()
        
        dates = []
        for i in range(7):
            date = today + timedelta(days=i)
            day_name = date.strftime('%A')
            if i == 0:
                display = f"Today ({day_name})"
            elif i == 1:
                display = f"Tomorrow ({day_name})"
            else:
                display = date.strftime('%A, %b %d')
            
            dates.append(ButtonOption(
                value=date.strftime('%Y-%m-%d'),
                display=display
            ))
        
        return dates
    
    def _get_time_buttons(self) -> List[ButtonOption]:
        """Get common time slot buttons"""
        times = [
            ('11:00', '11:00 AM'),
            ('12:00', '12:00 PM (Noon)'),
            ('13:00', '1:00 PM'),
            ('17:00', '5:00 PM'),
            ('18:00', '6:00 PM'),
            ('19:00', '7:00 PM'),
            ('20:00', '8:00 PM'),
            ('21:00', '9:00 PM'),
        ]
        return [ButtonOption(value=v, display=d) for v, d in times]
    
    def _get_guest_buttons(self) -> List[ButtonOption]:
        """Get guest count buttons 1-8 plus large party option"""
        buttons = []
        for i in range(1, 9):
            buttons.append(ButtonOption(
                value=str(i),
                display=f"{i} guest{'s' if i > 1 else ''}"
            ))
        buttons.append(ButtonOption(
            value='9+',
            display='9+ guests (large party)'
        ))
        return buttons
    
    def _get_yes_no_buttons(self) -> List[ButtonOption]:
        """Get yes/no confirmation buttons"""
        return [
            ButtonOption(value='yes', display='✓ Yes, that\'s correct'),
            ButtonOption(value='no', display='✗ No, let me update')
        ]
    
    def _get_confirm_buttons(self) -> List[ButtonOption]:
        """Get final confirmation buttons"""
        return [
            ButtonOption(value='confirm', display='✓ Confirm Reservation'),
            ButtonOption(value='cancel', display='✗ Cancel')
        ]
    
    def _get_special_request_buttons(self) -> List[ButtonOption]:
        """Get special request options"""
        return [
            ButtonOption(value='none', display='No special requests'),
            ButtonOption(value='window', display='Window seat preferred'),
            ButtonOption(value='quiet', display='Quiet area preferred'),
            ButtonOption(value='birthday', display='Birthday celebration'),
            ButtonOption(value='anniversary', display='Anniversary'),
            ButtonOption(value='other', display='Other (type your request)')
        ]
    
    def _check_availability(self, date_str: str, time_str: str, party_size: int) -> Dict[str, Any]:
        """Check table availability"""
        with self.app.app_context():
            try:
                reservation_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                reservation_time = datetime.strptime(time_str, '%H:%M').time()
                
                tables = Table.query.filter_by(
                    restaurant_id=self.restaurant_id,
                    is_active=True
                ).filter(Table.capacity >= party_size).all()
                
                if not tables:
                    return {'available': False, 'reason': f"No tables can accommodate {party_size} guests"}
                
                available_tables = []
                for table in tables:
                    existing = Reservation.query.filter_by(
                        table_id=table.id,
                        reservation_date=reservation_date,
                        reservation_time=reservation_time
                    ).filter(Reservation.status.in_(['pending', 'confirmed'])).first()
                    
                    if not existing:
                        available_tables.append({
                            'id': table.id,
                            'name': table.name,
                            'capacity': table.capacity,
                            'location': table.location
                        })
                
                if available_tables:
                    return {'available': True, 'tables': available_tables}
                else:
                    return {'available': False, 'reason': 'All tables are booked for this time'}
            except Exception as e:
                return {'available': False, 'reason': str(e)}
    
    def _make_reservation(self, booking: BookingDetails) -> Dict[str, Any]:
        """Create the reservation in the database"""
        with self.app.app_context():
            try:
                reservation_date = datetime.strptime(booking.date, '%Y-%m-%d').date()
                reservation_time = datetime.strptime(booking.time, '%H:%M').time()
                party_size = booking.guests
                
                # Find available table
                tables = Table.query.filter_by(
                    restaurant_id=self.restaurant_id,
                    is_active=True
                ).filter(Table.capacity >= party_size).order_by(Table.capacity).all()
                
                selected_table = None
                for table in tables:
                    existing = Reservation.query.filter_by(
                        table_id=table.id,
                        reservation_date=reservation_date,
                        reservation_time=reservation_time
                    ).filter(Reservation.status.in_(['pending', 'confirmed'])).first()
                    
                    if not existing:
                        selected_table = table
                        break
                
                if not selected_table:
                    return {'success': False, 'error': 'No tables available for this time'}
                
                # Create reservation
                reservation = Reservation(
                    restaurant_id=self.restaurant_id,
                    table_id=selected_table.id,
                    customer_name=booking.customer_name,
                    customer_phone=booking.customer_phone,
                    customer_email=booking.customer_email or '',
                    party_size=party_size,
                    reservation_date=reservation_date,
                    reservation_time=reservation_time,
                    special_requests=booking.special_requests or '',
                    status='confirmed',
                    source='ai_assistant'
                )
                db.session.add(reservation)
                
                # Update trial booking count if applicable
                restaurant = Restaurant.query.get(self.restaurant_id)
                if restaurant and restaurant.tenant:
                    tenant = restaurant.tenant
                    if hasattr(tenant, 'trial_booking_count'):
                        tenant.trial_booking_count += 1
                        db.session.add(tenant)
                
                db.session.commit()
                
                return {
                    'success': True,
                    'reservation_id': reservation.id,
                    'table_name': selected_table.name,
                    'table_location': selected_table.location
                }
            except Exception as e:
                db.session.rollback()
                return {'success': False, 'error': str(e)}
    
    def _restore_state(self, conversation_history: List[Dict]) -> ConversationState:
        """Restore conversation state from history"""
        if conversation_history:
            for msg in reversed(conversation_history):
                if msg.get('role') == 'assistant' and msg.get('conversation_state'):
                    try:
                        return ConversationState.model_validate(msg['conversation_state'])
                    except Exception:
                        pass
        
        # Return fresh state with customer info from incoming message
        state = ConversationState(
            restaurant_id=self.restaurant_id,
            timezone=self._timezone
        )
        
        # Pre-populate customer info if available
        if self._customer_info:
            state.incoming_customer_name = self._customer_info.name
            state.incoming_customer_phone = self._customer_info.phone
        
        return state
    
    def chat_sync(self, message: str, session_id: Optional[str] = None,
                  conversation_history: List[Dict] = None,
                  sender_name: Optional[str] = None,
                  sender_phone: Optional[str] = None) -> str:
        """
        Process user message and return response with buttons.
        Uses Pydantic models for state management.
        
        Args:
            message: The user's message
            session_id: Optional session ID for conversation continuity
            conversation_history: Previous conversation messages with state
            sender_name: Customer name from incoming message (Chatwoot)
            sender_phone: Customer phone from incoming message (Chatwoot)
        
        Returns:
            JSON string with response, buttons, and conversation state
        """
        # Initialize restaurant info and timezone
        self._get_restaurant_info()
        restaurant_name = self._restaurant_info.get('name', 'our restaurant')
        
        # Restore state from conversation history
        conv_state = self._restore_state(conversation_history or [])
        booking = conv_state.booking_details
        
        # Update customer info from sender if provided
        if sender_name and not conv_state.incoming_customer_name:
            conv_state.incoming_customer_name = sender_name
        if sender_phone and not conv_state.incoming_customer_phone:
            conv_state.incoming_customer_phone = sender_phone
        
        message_lower = message.lower().strip()
        
        # =================================================================
        # STATE MACHINE: Process based on current state
        # =================================================================
        
        # --- AWAITING DATE ---
        if conv_state.state == BookingState.AWAITING_DATE:
            parsed = DateTimeParser.parse_date(message, self._timezone)
            if parsed:
                date_obj, date_display = parsed
                booking.date = date_obj.strftime('%Y-%m-%d')
                booking.date_display = date_display
                booking.date_raw_input = message
                conv_state.state = BookingState.AWAITING_TIME
                conv_state.booking_details = booking
                
                response = AssistantResponse(
                    text=f"Great! You've selected {date_display}.\n\nWhat time would you like to dine? You can type a time (e.g., '7pm', '19:30') or select from common times:",
                    buttons=self._get_time_buttons(),
                    button_type='time',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            else:
                # Couldn't parse date, ask again
                response = AssistantResponse(
                    text="I couldn't understand that date. Please try formats like:\n• Today, Tomorrow\n• Monday, Tuesday, etc.\n• January 25, Jan 25th\n• 01/25/2026 or 2026-01-25\n\nOr select from the options below:",
                    buttons=self._get_next_7_dates(),
                    button_type='date',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        # --- AWAITING TIME ---
        elif conv_state.state == BookingState.AWAITING_TIME:
            parsed = DateTimeParser.parse_time(message)
            if parsed:
                hour, minute, time_display = parsed
                booking.time = f"{hour:02d}:{minute:02d}"
                booking.time_display = time_display
                booking.time_raw_input = message
                conv_state.state = BookingState.AWAITING_GUESTS
                conv_state.booking_details = booking
                
                response = AssistantResponse(
                    text=f"Perfect! {booking.date_display} at {time_display}.\n\nHow many guests will be dining?",
                    buttons=self._get_guest_buttons(),
                    button_type='guests',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            else:
                response = AssistantResponse(
                    text="I couldn't understand that time. Please try formats like:\n• 7pm, 7:30 PM\n• 19:00, 19:30\n• noon, evening\n\nOr select from the options below:",
                    buttons=self._get_time_buttons(),
                    button_type='time',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        # --- AWAITING GUESTS ---
        elif conv_state.state == BookingState.AWAITING_GUESTS:
            # Handle large party request
            if message == '9+' or 'large' in message_lower or int(message) > self.MAX_GUESTS_WITHOUT_HANDOVER if message.isdigit() else False:
                booking.requires_human_handover = True
                booking.handover_reason = "Large party (9+ guests)"
                conv_state.state = BookingState.HANDOVER_TO_HUMAN
                conv_state.booking_details = booking
                
                # Pre-fill customer info from incoming message
                customer_name = conv_state.incoming_customer_name or "Guest"
                customer_phone = conv_state.incoming_customer_phone or "Not provided"
                
                response = AssistantResponse(
                    text=f"For parties of 9 or more guests, our staff will personally assist you to ensure the best experience.\n\n"
                         f"📋 **Your Request:**\n"
                         f"• Date: {booking.date_display}\n"
                         f"• Time: {booking.time_display}\n"
                         f"• Party size: Large group (9+)\n\n"
                         f"📞 **Your Contact Info:**\n"
                         f"• Name: {customer_name}\n"
                         f"• Phone: {customer_phone}\n\n"
                         f"Our team will contact you within 24 hours to confirm availability and finalize your reservation. "
                         f"Is there anything else you'd like us to know?",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            
            # Parse guest count
            try:
                # Handle "X guests" or just "X"
                guests_text = message.replace('guests', '').replace('guest', '').strip()
                guests = int(guests_text)
                
                if guests < 1:
                    raise ValueError("Invalid guest count")
                
                if guests > self.MAX_GUESTS_WITHOUT_HANDOVER:
                    # Redirect to human handover
                    booking.guests = guests
                    booking.requires_human_handover = True
                    booking.handover_reason = f"Large party ({guests} guests)"
                    conv_state.state = BookingState.HANDOVER_TO_HUMAN
                    conv_state.booking_details = booking
                    
                    customer_name = conv_state.incoming_customer_name or "Guest"
                    customer_phone = conv_state.incoming_customer_phone or "Not provided"
                    
                    response = AssistantResponse(
                        text=f"For parties of {guests} guests, our staff will personally assist you.\n\n"
                             f"📋 **Your Request:**\n"
                             f"• Date: {booking.date_display}\n"
                             f"• Time: {booking.time_display}\n"
                             f"• Guests: {guests}\n\n"
                             f"📞 **Your Contact Info:**\n"
                             f"• Name: {customer_name}\n"
                             f"• Phone: {customer_phone}\n\n"
                             f"Our team will contact you within 24 hours. Is there anything else you'd like us to know?",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
                
                booking.guests = guests
                conv_state.state = BookingState.AWAITING_NAME_CONFIRMATION
                conv_state.booking_details = booking
                
                # Use customer info from incoming message
                customer_name = conv_state.incoming_customer_name
                customer_phone = conv_state.incoming_customer_phone
                
                if customer_name and customer_phone:
                    # We have both - ask for confirmation
                    booking.customer_name = customer_name
                    booking.customer_phone = customer_phone
                    
                    response = AssistantResponse(
                        text=f"I have your contact information:\n\n"
                             f"👤 **Name:** {customer_name}\n"
                             f"📞 **Phone:** {customer_phone}\n\n"
                             f"Is this correct for the reservation?",
                        buttons=self._get_yes_no_buttons(),
                        button_type='confirm_contact',
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
                else:
                    # Need to collect name
                    response = AssistantResponse(
                        text="May I have your name for the reservation?",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
                    
            except ValueError:
                response = AssistantResponse(
                    text="Please select the number of guests:",
                    buttons=self._get_guest_buttons(),
                    button_type='guests',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        # --- AWAITING NAME CONFIRMATION ---
        elif conv_state.state == BookingState.AWAITING_NAME_CONFIRMATION:
            if message_lower in ['yes', 'correct', 'that\'s right', 'confirm', 'y']:
                # Contact info confirmed, move to special requests
                conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                conv_state.booking_details = booking
                
                response = AssistantResponse(
                    text="Great! Do you have any special requests for your reservation?\n\n"
                         "(e.g., window seat, birthday celebration, dietary requirements, etc.)",
                    buttons=self._get_special_request_buttons(),
                    button_type='special_requests',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            
            elif message_lower in ['no', 'wrong', 'incorrect', 'update', 'change', 'n']:
                # Need to update contact info
                booking.customer_name = None
                booking.customer_phone = None
                conv_state.booking_details = booking
                
                response = AssistantResponse(
                    text="No problem! Please provide the name for the reservation:",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            
            else:
                # User is providing their name directly
                if len(message) >= 2:
                    booking.customer_name = message.strip()
                    conv_state.booking_details = booking
                    
                    # Check if we have phone from incoming message
                    if conv_state.incoming_customer_phone:
                        booking.customer_phone = conv_state.incoming_customer_phone
                        conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                        
                        response = AssistantResponse(
                            text=f"Thank you, {booking.customer_name.split()[0]}!\n\n"
                                 f"Do you have any special requests for your reservation?",
                            buttons=self._get_special_request_buttons(),
                            button_type='special_requests',
                            conversation_state=conv_state
                        )
                        return response.model_dump_json()
                    else:
                        # Need phone number
                        first_name = booking.customer_name.split()[0]
                        response = AssistantResponse(
                            text=f"Thanks, {first_name}! What's the best phone number to reach you?",
                            conversation_state=conv_state
                        )
                        return response.model_dump_json()
                
                # Check if this might be a phone number
                digits = ''.join(c for c in message if c.isdigit())
                if len(digits) >= 7:
                    booking.customer_phone = message.strip()
                    conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                    conv_state.booking_details = booking
                    
                    response = AssistantResponse(
                        text="Do you have any special requests for your reservation?",
                        buttons=self._get_special_request_buttons(),
                        button_type='special_requests',
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
        
        # --- AWAITING SPECIAL REQUESTS ---
        elif conv_state.state == BookingState.AWAITING_SPECIAL_REQUESTS:
            # Handle special request buttons
            special_request_map = {
                'none': None,
                'window': 'Window seat preferred',
                'quiet': 'Quiet area preferred',
                'birthday': 'Birthday celebration',
                'anniversary': 'Anniversary celebration',
                'other': None  # Will prompt for text input
            }
            
            if message_lower in special_request_map:
                if message_lower == 'other':
                    response = AssistantResponse(
                        text="Please type your special request:",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
                
                booking.special_requests = special_request_map[message_lower]
            else:
                # User typed their own request
                booking.special_requests = message.strip() if message_lower != 'none' else None
            
            conv_state.state = BookingState.AWAITING_FINAL_CONFIRMATION
            conv_state.booking_details = booking
            
            # Build confirmation summary
            summary = (
                f"📋 **Reservation Summary**\n\n"
                f"📅 **Date:** {booking.date_display}\n"
                f"🕐 **Time:** {booking.time_display}\n"
                f"👥 **Guests:** {booking.guests}\n"
                f"👤 **Name:** {booking.customer_name}\n"
                f"📞 **Phone:** {booking.customer_phone}\n"
            )
            
            if booking.special_requests:
                summary += f"📝 **Special Requests:** {booking.special_requests}\n"
            
            summary += f"\n🍽️ **Restaurant:** {restaurant_name}\n\n"
            summary += "Please confirm your reservation:"
            
            response = AssistantResponse(
                text=summary,
                buttons=self._get_confirm_buttons(),
                button_type='final_confirm',
                conversation_state=conv_state
            )
            return response.model_dump_json()
        
        # --- AWAITING FINAL CONFIRMATION ---
        elif conv_state.state == BookingState.AWAITING_FINAL_CONFIRMATION:
            if message_lower in ['confirm', 'yes', 'book', 'reserve', 'y']:
                # Make the reservation
                result = self._make_reservation(booking)
                
                if result['success']:
                    conv_state.state = BookingState.COMPLETED
                    conv_state.booking_details = booking
                    
                    response = AssistantResponse(
                        text=f"🎉 **Reservation Confirmed!**\n\n"
                             f"📅 {booking.date_display} at {booking.time_display}\n"
                             f"👥 {booking.guests} guest{'s' if booking.guests > 1 else ''}\n"
                             f"📍 {result['table_name']} ({result['table_location']})\n"
                             f"🎫 Confirmation #{result['reservation_id']}\n\n"
                             f"We look forward to seeing you at {restaurant_name}!\n\n"
                             f"Need to modify or cancel? Just reply to this message.",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
                else:
                    response = AssistantResponse(
                        text=f"I'm sorry, there was an issue: {result.get('error', 'Unknown error')}.\n\n"
                             "Please try again or contact the restaurant directly.",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
            
            elif message_lower in ['cancel', 'no', 'stop', 'n']:
                conv_state.state = BookingState.INITIAL
                conv_state.booking_details = BookingDetails()
                
                response = AssistantResponse(
                    text="No problem! Your reservation has been cancelled.\n\n"
                         "Is there anything else I can help you with?",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            
            else:
                # Unclear response, ask again
                response = AssistantResponse(
                    text="Would you like to confirm this reservation?",
                    buttons=self._get_confirm_buttons(),
                    button_type='final_confirm',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        # --- HANDOVER TO HUMAN ---
        elif conv_state.state == BookingState.HANDOVER_TO_HUMAN:
            # Store any additional notes from the customer
            if message.strip():
                if booking.special_requests:
                    booking.special_requests += f"\n\nAdditional notes: {message}"
                else:
                    booking.special_requests = message
                conv_state.booking_details = booking
            
            conv_state.state = BookingState.COMPLETED
            
            response = AssistantResponse(
                text="Thank you! I've noted your request. Our team will be in touch within 24 hours.\n\n"
                     "Is there anything else I can help you with?",
                conversation_state=conv_state
            )
            return response.model_dump_json()
        
        # --- COMPLETED STATE ---
        elif conv_state.state == BookingState.COMPLETED:
            # Check if user wants to make another reservation
            reservation_keywords = ['reserv', 'book', 'table', 'another', 'new booking']
            if any(kw in message_lower for kw in reservation_keywords):
                conv_state.state = BookingState.AWAITING_DATE
                conv_state.booking_details = BookingDetails()
                
                response = AssistantResponse(
                    text="I'd be happy to help you make another reservation!\n\n"
                         "When would you like to dine?",
                    buttons=self._get_next_7_dates(),
                    button_type='date',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        # =================================================================
        # INITIAL STATE: Detect intent
        # =================================================================
        
        if conv_state.state == BookingState.INITIAL:
            knowledge_base = self._restaurant_info.get('knowledge_base', '')
            
            # Check for question keywords
            question_keywords = ['menu', 'hour', 'open', 'close', 'location', 'address', 'park', 
                               'vegetarian', 'vegan', 'gluten', 'allerg', 'price', 'cost',
                               'dress', 'code', 'private', 'event', 'catering', 'takeout',
                               'delivery', 'outdoor', 'patio', 'wheelchair', 'accessible',
                               'happy hour', 'special', 'wine', 'drink', 'dessert', 'appetizer',
                               'what do you', 'do you have', 'can i', 'is there', 'where',
                               'time', 'when', 'how', 'what', 'why', 'who', 'which', 'tell me',
                               'info', 'about', 'contact', 'phone', 'email', 'website']
            
            is_question = any(kw in message_lower for kw in question_keywords) or '?' in message
            
            # Check for reservation intent
            reservation_keywords = ['reserv', 'book', 'table for', 'make a booking', 'get a table', 
                                   'dinner', 'lunch', 'brunch', 'party of']
            is_reservation_request = any(kw in message_lower for kw in reservation_keywords)
            
            # Handle questions first (if not a reservation request)
            if is_question and not is_reservation_request:
                if knowledge_base:
                    answer = self._answer_from_knowledge_base(message, knowledge_base)
                    if answer:
                        response = AssistantResponse(
                            text=answer,
                            conversation_state=conv_state
                        )
                        return response.model_dump_json()
                else:
                    answer = self._answer_general_question(message)
                    if answer:
                        response = AssistantResponse(
                            text=answer,
                            conversation_state=conv_state
                        )
                        return response.model_dump_json()
            
            # Handle reservation request
            if is_reservation_request:
                conv_state.state = BookingState.AWAITING_DATE
                conv_state.booking_details = BookingDetails()
                
                response = AssistantResponse(
                    text=f"I'd be happy to help you make a reservation at {restaurant_name}!\n\n"
                         "When would you like to dine? You can type a date (e.g., 'tomorrow', 'Friday', 'January 25') "
                         "or select from the options below:",
                    buttons=self._get_next_7_dates(),
                    button_type='date',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        # Default welcome response
        response = AssistantResponse(
            text=f"Welcome to {restaurant_name}! 👋\n\n"
                 "I'm your AI reservation assistant. I can help you:\n"
                 "• Make a table reservation\n"
                 "• Answer questions about our restaurant\n"
                 "• Check availability\n\n"
                 "How can I help you today?",
            conversation_state=conv_state
        )
        return response.model_dump_json()
    
    def _answer_general_question(self, question: str) -> Optional[str]:
        """Use OpenAI to answer a general question when no knowledge base is available."""
        restaurant_name = self._restaurant_info.get('name', 'our restaurant')
        restaurant_address = self._restaurant_info.get('address', '')
        restaurant_city = self._restaurant_info.get('city', '')
        restaurant_phone = self._restaurant_info.get('phone', '')
        
        try:
            system_prompt = f"""You are a helpful assistant for {restaurant_name}.
You can help with general questions and making reservations.

Restaurant Info:
- Name: {restaurant_name}
- Address: {restaurant_address}, {restaurant_city}
- Phone: {restaurant_phone}

For questions you don't know the answer to, politely say you don't have that specific information
and offer to help with making a reservation instead.

Keep answers concise and friendly. Do not make up specific information like hours or menu items."""
            
            response = self._client.chat.completions.create(
                model="gpt-4.1-nano",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}
                ],
                max_tokens=300,
                temperature=0.7
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            return None
    
    def _answer_from_knowledge_base(self, question: str, knowledge_base: str) -> Optional[str]:
        """Use OpenAI to answer a question based on the restaurant's knowledge base."""
        restaurant_name = self._restaurant_info.get('name', 'our restaurant')
        
        try:
            system_prompt = f"""You are a helpful assistant for {restaurant_name}. 
Answer the customer's question based ONLY on the following knowledge base information.
If the answer is not in the knowledge base, say you don't have that information and offer to help with a reservation instead.
Keep answers concise and friendly. Do not make up information.

--- KNOWLEDGE BASE ---
{knowledge_base}
--- END KNOWLEDGE BASE ---"""
            
            response = self._client.chat.completions.create(
                model="gpt-4.1-nano",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}
                ],
                max_tokens=300,
                temperature=0.7
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            return None


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def get_assistant(restaurant_id: int, app=None, 
                  customer_name: Optional[str] = None,
                  customer_phone: Optional[str] = None) -> ReservationAssistant:
    """
    Get a reservation assistant for a restaurant.
    
    Args:
        restaurant_id: The restaurant ID
        app: Flask app instance
        customer_name: Optional customer name from incoming message
        customer_phone: Optional customer phone from incoming message
    
    Returns:
        ReservationAssistant instance
    """
    customer_info = None
    if customer_name or customer_phone:
        customer_info = CustomerInfo(name=customer_name, phone=customer_phone)
    
    return ReservationAssistant(restaurant_id, app, customer_info)
