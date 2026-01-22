"""
AI Reservation Assistant Service with OpenAI-Based Entity Extraction
Uses Pydantic models for structured data and OpenAI for intelligent understanding.

KEY FEATURES:
1. State persistence using database to remember booking details across messages
2. Context-aware parsing for direct answers (e.g., "6" when asked for guest count)
3. OpenAI extraction for complex natural language understanding

Reservation Flow:
1. Extract all available info from user message using OpenAI
2. Store state in database after each response
3. Retrieve state on next message to continue conversation
4. Ask only for missing required fields
5. Confirm name/phone from incoming message
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

# Import memory service
try:
    from app.services.memory_service import get_memory_service, format_memories_for_context
    MEMORY_AVAILABLE = True
except ImportError:
    MEMORY_AVAILABLE = False
    print("WARNING: Memory service not available", flush=True)


# =============================================================================
# PYDANTIC MODELS FOR STRUCTURED DATA
# =============================================================================

class BookingState(str, Enum):
    """Enum for booking conversation states"""
    INITIAL = 'initial'
    COLLECTING_INFO = 'collecting_info'
    AWAITING_DATE = 'awaiting_date'
    AWAITING_TIME = 'awaiting_time'
    AWAITING_GUESTS = 'awaiting_guests'
    AWAITING_NAME_CONFIRMATION = 'awaiting_name_confirmation'
    AWAITING_NAME_INPUT = 'awaiting_name_input'
    AWAITING_PHONE_INPUT = 'awaiting_phone_input'
    AWAITING_SPECIAL_REQUESTS = 'awaiting_special_requests'
    AWAITING_FINAL_CONFIRMATION = 'awaiting_final_confirmation'
    HANDOVER_TO_HUMAN = 'handover_to_human'
    COMPLETED = 'completed'


class ExtractedReservationInfo(BaseModel):
    """Pydantic model for reservation info extracted from user message by OpenAI"""
    has_reservation_intent: bool = Field(False, description="Whether user wants to make a reservation")
    date: Optional[str] = Field(None, description="Extracted date in YYYY-MM-DD format")
    date_display: Optional[str] = Field(None, description="Human-readable date")
    time: Optional[str] = Field(None, description="Extracted time in HH:MM format (24h)")
    time_display: Optional[str] = Field(None, description="Human-readable time (12h)")
    guests: Optional[int] = Field(None, description="Number of guests")
    name: Optional[str] = Field(None, description="Customer name if mentioned")
    is_question: bool = Field(False, description="Whether this is a question about the restaurant")
    question_topic: Optional[str] = Field(None, description="Topic of the question if any")


class BookingDetails(BaseModel):
    """Pydantic model for complete booking details"""
    date: Optional[str] = Field(None, description="Reservation date in YYYY-MM-DD format")
    date_display: Optional[str] = Field(None, description="Human-readable date display")
    time: Optional[str] = Field(None, description="Reservation time in HH:MM format (24h)")
    time_display: Optional[str] = Field(None, description="Human-readable time display (12-hour)")
    guests: Optional[int] = Field(None, description="Number of guests", ge=1, le=50)
    customer_name: Optional[str] = Field(None, description="Customer name")
    customer_phone: Optional[str] = Field(None, description="Customer phone number")
    customer_email: Optional[str] = Field(None, description="Customer email (optional)")
    special_requests: Optional[str] = Field(None, description="Special requests or notes")
    requires_human_handover: bool = Field(False, description="Whether this booking requires human staff")
    handover_reason: Optional[str] = Field(None, description="Reason for human handover")
    
    def get_missing_fields(self) -> List[str]:
        """Return list of missing required fields"""
        missing = []
        if not self.date:
            missing.append('date')
        if not self.time:
            missing.append('time')
        if not self.guests:
            missing.append('guests')
        return missing
    
    def is_complete(self) -> bool:
        """Check if all required fields are filled"""
        return bool(self.date and self.time and self.guests)


class ConversationState(BaseModel):
    """Pydantic model for conversation state - persisted between requests"""
    state: BookingState = Field(default=BookingState.INITIAL, description="Current booking state")
    booking_details: BookingDetails = Field(default_factory=BookingDetails, description="Collected booking details")
    restaurant_id: int = Field(..., description="Restaurant ID")
    timezone: str = Field(default='UTC', description="Restaurant timezone")
    incoming_customer_name: Optional[str] = Field(None, description="Name from incoming message")
    incoming_customer_phone: Optional[str] = Field(None, description="Phone from incoming message")
    last_question: Optional[str] = Field(None, description="What we last asked the user (date/time/guests)")


class ButtonOption(BaseModel):
    """Pydantic model for a button option"""
    value: str = Field(..., description="Value sent when button is clicked")
    display: str = Field(..., description="Display text for the button")


class AssistantResponse(BaseModel):
    """Pydantic model for assistant response"""
    text: str = Field(..., description="Response text message")
    buttons: Optional[List[ButtonOption]] = Field(None, description="Optional buttons to display")
    button_type: Optional[str] = Field(None, description="Type of buttons")
    conversation_state: ConversationState = Field(..., description="Current conversation state to persist")


# =============================================================================
# STATE PERSISTENCE FUNCTIONS
# =============================================================================

def save_conversation_state(restaurant_id: int, conversation_id: str, state: ConversationState, app=None):
    """Save conversation state to database for persistence across messages."""
    try:
        if app:
            with app.app_context():
                _do_save_state(restaurant_id, conversation_id, state)
        else:
            _do_save_state(restaurant_id, conversation_id, state)
    except Exception as e:
        print(f"Error saving conversation state: {e}", flush=True)


def _do_save_state(restaurant_id: int, conversation_id: str, state: ConversationState):
    """Internal function to save state within app context."""
    # Look for existing state record
    existing = AIConversation.query.filter_by(
        restaurant_id=restaurant_id,
        conversation_type=f'state_{conversation_id}'
    ).first()
    
    state_json = state.model_dump_json()
    
    if existing:
        existing.transcript = state_json
        existing.updated_at = datetime.utcnow()
    else:
        new_record = AIConversation(
            restaurant_id=restaurant_id,
            conversation_type=f'state_{conversation_id}',
            transcript=state_json,
            tokens_used=0
        )
        db.session.add(new_record)
    
    db.session.commit()
    print(f"Saved state for conversation {conversation_id}: {state.state}, last_question={state.last_question}", flush=True)


def load_conversation_state(restaurant_id: int, conversation_id: str, app=None) -> Optional[ConversationState]:
    """Load conversation state from database."""
    try:
        if app:
            with app.app_context():
                return _do_load_state(restaurant_id, conversation_id)
        else:
            return _do_load_state(restaurant_id, conversation_id)
    except Exception as e:
        print(f"Error loading conversation state: {e}", flush=True)
        return None


def _do_load_state(restaurant_id: int, conversation_id: str) -> Optional[ConversationState]:
    """Internal function to load state within app context."""
    record = AIConversation.query.filter_by(
        restaurant_id=restaurant_id,
        conversation_type=f'state_{conversation_id}'
    ).first()
    
    if record and record.transcript:
        try:
            state = ConversationState.model_validate_json(record.transcript)
            print(f"Loaded state for conversation {conversation_id}: {state.state}, last_question={state.last_question}", flush=True)
            print(f"Booking details: date={state.booking_details.date}, time={state.booking_details.time}, guests={state.booking_details.guests}", flush=True)
            return state
        except Exception as e:
            print(f"Error parsing saved state: {e}", flush=True)
    
    return None


def clear_conversation_state(restaurant_id: int, conversation_id: str, app=None):
    """Clear conversation state from database (e.g., after completion or cancellation)."""
    try:
        if app:
            with app.app_context():
                _do_clear_state(restaurant_id, conversation_id)
        else:
            _do_clear_state(restaurant_id, conversation_id)
    except Exception as e:
        print(f"Error clearing conversation state: {e}", flush=True)


def _do_clear_state(restaurant_id: int, conversation_id: str):
    """Internal function to clear state within app context."""
    AIConversation.query.filter_by(
        restaurant_id=restaurant_id,
        conversation_type=f'state_{conversation_id}'
    ).delete()
    db.session.commit()
    print(f"Cleared state for conversation {conversation_id}", flush=True)


# =============================================================================
# CONTEXT-AWARE PARSING FUNCTIONS
# =============================================================================

def is_phone_number_like(text: str) -> bool:
    """
    Check if a text string looks like a phone number rather than a name.
    Returns True if the text appears to be a phone number.
    """
    if not text:
        return True
    
    # Remove common phone formatting characters
    cleaned = text.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('+', '')
    
    # If mostly digits, it's a phone number
    digit_count = sum(1 for c in cleaned if c.isdigit())
    if len(cleaned) > 0 and digit_count / len(cleaned) > 0.6:
        return True
    
    # Check for phone patterns like "+46 73 540 80 23" or "46735408023"
    if text.startswith('+') or (len(cleaned) >= 7 and cleaned.isdigit()):
        return True
    
    return False


def parse_number_from_text(text: str) -> Optional[int]:
    """
    Extract a number from text, handling both digits and word forms.
    Returns None if no valid number found.
    """
    text = text.lower().strip()
    
    # Word to number mapping
    word_numbers = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
        'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20,
        # Common variations
        'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
        'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10,
        # Handle transcription errors
        'sex': 6, 'sick': 6, 'sicks': 6,  # "six" misheard
        'to': 2, 'too': 2, 'for': 4,  # common mishearings
    }
    
    # Check for word numbers
    for word, num in word_numbers.items():
        if word in text.split():
            return num
    
    # Check for digits
    digits = re.findall(r'\d+', text)
    if digits:
        num = int(digits[0])
        if 1 <= num <= 50:  # Reasonable guest count
            return num
    
    return None


def parse_date_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse date from text, handling various formats.
    Returns (date_str in YYYY-MM-DD, display_str) or (None, None).
    """
    text = text.lower().strip()
    today = datetime.now()
    
    # Handle relative dates
    if 'today' in text:
        return today.strftime('%Y-%m-%d'), f"Today ({today.strftime('%A')})"
    
    if 'tomorrow' in text:
        tomorrow = today + timedelta(days=1)
        return tomorrow.strftime('%Y-%m-%d'), f"Tomorrow ({tomorrow.strftime('%A')})"
    
    # Handle weekday names
    weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    for i, day in enumerate(weekdays):
        if day in text:
            # Find next occurrence of this weekday
            days_ahead = i - today.weekday()
            if days_ahead <= 0:  # Target day already happened this week
                days_ahead += 7
            target_date = today + timedelta(days=days_ahead)
            return target_date.strftime('%Y-%m-%d'), target_date.strftime('%A, %B %d')
    
    # Handle YYYY-MM-DD format
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    if match:
        try:
            date = datetime.strptime(match.group(0), '%Y-%m-%d')
            return match.group(0), date.strftime('%A, %B %d')
        except ValueError:
            pass
    
    return None, None


def parse_time_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse time from text, handling various formats.
    Returns (time_str in HH:MM, display_str) or (None, None).
    """
    text = text.lower().strip()
    
    # Handle common time words
    time_words = {
        'noon': ('12:00', '12:00 PM'),
        'midnight': ('00:00', '12:00 AM'),
        'lunch': ('12:00', '12:00 PM'),
        'dinner': ('19:00', '7:00 PM'),
        'evening': ('19:00', '7:00 PM'),
    }
    
    for word, (time_24, time_12) in time_words.items():
        if word in text:
            return time_24, time_12
    
    # Handle HH:MM format (24h)
    match = re.search(r'(\d{1,2}):(\d{2})', text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            time_24 = f"{hour:02d}:{minute:02d}"
            if hour == 0:
                time_12 = f"12:{minute:02d} AM"
            elif hour < 12:
                time_12 = f"{hour}:{minute:02d} AM"
            elif hour == 12:
                time_12 = f"12:{minute:02d} PM"
            else:
                time_12 = f"{hour-12}:{minute:02d} PM"
            return time_24, time_12
    
    # Handle "X pm" or "X am" format
    match = re.search(r'(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)', text)
    if match:
        hour = int(match.group(1))
        is_pm = 'p' in match.group(2).lower()
        
        if is_pm and hour != 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0
        
        time_24 = f"{hour:02d}:00"
        if hour == 0:
            time_12 = "12:00 AM"
        elif hour < 12:
            time_12 = f"{hour}:00 AM"
        elif hour == 12:
            time_12 = "12:00 PM"
        else:
            time_12 = f"{hour-12}:00 PM"
        return time_24, time_12
    
    # Handle just a number (assume PM for typical dinner hours 5-10)
    match = re.search(r'^(\d{1,2})$', text)
    if match:
        hour = int(match.group(1))
        if 1 <= hour <= 10:  # Assume PM for dinner hours
            hour_24 = hour + 12 if hour != 12 else 12
            return f"{hour_24:02d}:00", f"{hour}:00 PM"
    
    return None, None


# =============================================================================
# RESERVATION ASSISTANT CLASS
# =============================================================================

class ReservationAssistant:
    """AI-powered reservation assistant with OpenAI-based entity extraction and state persistence"""
    
    MAX_GUESTS_WITHOUT_HANDOVER = 8
    
    def __init__(self, restaurant_id: int, app=None, customer_info: Optional[Dict] = None):
        self.restaurant_id = restaurant_id
        self.app = app
        self._restaurant_info = None
        self._timezone = 'UTC'
        self._client = OpenAI()
        self._customer_info = customer_info or {}
        
        # Initialize memory service
        self._memory_service = None
        if MEMORY_AVAILABLE:
            try:
                self._memory_service = get_memory_service()
                print(f"Memory service initialized: {self._memory_service.is_available}", flush=True)
            except Exception as e:
                print(f"Failed to initialize memory service: {e}", flush=True)
    
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
    
    def _extract_reservation_info(self, message: str, context: Optional[str] = None) -> ExtractedReservationInfo:
        """
        Use OpenAI to extract reservation information from user message.
        Context helps OpenAI understand what the user is responding to.
        """
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        tomorrow_str = (today + timedelta(days=1)).strftime('%Y-%m-%d')
        
        context_hint = ""
        if context:
            context_hint = f"\nIMPORTANT CONTEXT: The user was just asked: \"{context}\". Their response should be interpreted in this context."
        
        system_prompt = f"""You are an AI that extracts reservation information from user messages.
Today's date is {today_str} ({today.strftime('%A')}).
Tomorrow is {tomorrow_str}.
{context_hint}

Extract the following information if present in the user's message:
1. has_reservation_intent: true if user wants to make a reservation, book a table, dine, etc.
2. date: Convert any date mention to YYYY-MM-DD format
   - "today" = {today_str}
   - "tomorrow" = {tomorrow_str}
   - "Friday" = next Friday's date
   - "January 25" = 2026-01-25 (use current/next year)
3. date_display: Human readable date like "Friday, January 24"
4. time: Convert any time to HH:MM 24-hour format
   - "6pm" = "18:00"
   - "7:30" = "19:30" (assume PM for evening times)
   - "noon" = "12:00"
   - "evening" = "19:00"
5. time_display: Human readable time like "6:00 PM"
6. guests: Number of people (look for "for 4", "party of 2", "2 people", "we will be 7", etc.)
   - If user says just a number like "6" or "six", extract it as guests
   - "Six" = 6 guests
   - Handle transcription errors: "sex" or "sick" likely means "six" = 6
7. name: Customer name if explicitly mentioned
8. is_question: true if user is asking a question about the restaurant (hours, menu, location, etc.)
9. question_topic: What the question is about if is_question is true

IMPORTANT: 
- "dine" means reservation intent
- "die" is likely a transcription error for "dine"
- Be generous in detecting reservation intent
- Extract ALL available information from the message
- If user gives just a number, it's likely answering a previous question about guests

Return a JSON object with these fields. Use null for missing fields."""

        try:
            response = self._client.chat.completions.create(
                model="gpt-4.1-nano",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Extract reservation info from: \"{message}\""}
                ],
                response_format={"type": "json_object"},
                max_tokens=500,
                temperature=0.1
            )
            
            result = json.loads(response.choices[0].message.content)
            print(f"OpenAI extraction result: {result}", flush=True)
            
            return ExtractedReservationInfo(
                has_reservation_intent=result.get('has_reservation_intent', False),
                date=result.get('date'),
                date_display=result.get('date_display'),
                time=result.get('time'),
                time_display=result.get('time_display'),
                guests=result.get('guests'),
                name=result.get('name'),
                is_question=result.get('is_question', False),
                question_topic=result.get('question_topic')
            )
            
        except Exception as e:
            print(f"OpenAI extraction error: {e}", flush=True)
            # Fallback to basic keyword detection
            message_lower = message.lower()
            has_intent = any(kw in message_lower for kw in ['reserv', 'book', 'table', 'dine', 'die', 'dinner', 'lunch'])
            is_question = '?' in message or any(kw in message_lower for kw in ['what', 'when', 'where', 'how', 'menu', 'hour'])
            
            return ExtractedReservationInfo(
                has_reservation_intent=has_intent,
                is_question=is_question
            )
    
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
            dates.append(ButtonOption(value=date.strftime('%Y-%m-%d'), display=display))
        return dates
    
    def _get_time_buttons(self) -> List[ButtonOption]:
        """Get common time slot buttons"""
        times = [
            ('12:00', '12:00 PM (Noon)'),
            ('18:00', '6:00 PM'),
            ('19:00', '7:00 PM'),
            ('20:00', '8:00 PM'),
        ]
        return [ButtonOption(value=v, display=d) for v, d in times]
    
    def _get_guest_buttons(self) -> List[ButtonOption]:
        """Get guest count buttons"""
        buttons = []
        for i in range(1, 9):
            buttons.append(ButtonOption(value=str(i), display=f"{i} guest{'s' if i > 1 else ''}"))
        buttons.append(ButtonOption(value='9+', display='9+ guests'))
        return buttons
    
    def _get_yes_no_buttons(self) -> List[ButtonOption]:
        """Get yes/no confirmation buttons"""
        return [
            ButtonOption(value='yes', display='Yes, correct'),
            ButtonOption(value='no', display='No, update')
        ]
    
    def _get_confirm_buttons(self) -> List[ButtonOption]:
        """Get final confirmation buttons"""
        return [
            ButtonOption(value='confirm', display='Confirm Reservation'),
            ButtonOption(value='cancel', display='Cancel')
        ]
    
    def _get_special_request_buttons(self) -> List[ButtonOption]:
        """Get special request options"""
        return [
            ButtonOption(value='none', display='No special requests'),
            ButtonOption(value='window', display='Window seat'),
            ButtonOption(value='quiet', display='Quiet area'),
            ButtonOption(value='birthday', display='Birthday'),
            ButtonOption(value='anniversary', display='Anniversary'),
            ButtonOption(value='other', display='Other (type below)')
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
                    return {'available': False, 'reason': f"No tables for {party_size} guests"}
                
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
                    return {'available': False, 'reason': 'All tables booked'}
            except Exception as e:
                return {'available': False, 'reason': str(e)}
    
    def _make_reservation(self, booking: BookingDetails) -> Dict[str, Any]:
        """Create the reservation in the database"""
        with self.app.app_context():
            try:
                reservation_date = datetime.strptime(booking.date, '%Y-%m-%d').date()
                reservation_time = datetime.strptime(booking.time, '%H:%M').time()
                party_size = booking.guests
                
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
                    return {'success': False, 'error': 'No tables available'}
                
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
    
    def _ask_for_missing_info(self, conv_state: ConversationState, booking: BookingDetails) -> AssistantResponse:
        """Generate response asking for the next missing piece of information"""
        missing = booking.get_missing_fields()
        
        if 'date' in missing:
            conv_state.last_question = 'date'
            conv_state.state = BookingState.AWAITING_DATE
            return AssistantResponse(
                text="When would you like to dine? You can type a date or select below:",
                buttons=self._get_next_7_dates(),
                button_type='date',
                conversation_state=conv_state
            )
        elif 'time' in missing:
            conv_state.last_question = 'time'
            conv_state.state = BookingState.AWAITING_TIME
            date_text = booking.date_display or booking.date
            return AssistantResponse(
                text=f"Great! {date_text}. What time would you prefer?",
                buttons=self._get_time_buttons(),
                button_type='time',
                conversation_state=conv_state
            )
        elif 'guests' in missing:
            conv_state.last_question = 'guests'
            conv_state.state = BookingState.AWAITING_GUESTS
            return AssistantResponse(
                text="How many guests will be dining?",
                buttons=self._get_guest_buttons(),
                button_type='guests',
                conversation_state=conv_state
            )
        
        # All info collected - shouldn't reach here
        return AssistantResponse(
            text="I have all the information needed.",
            conversation_state=conv_state
        )
    
    def _answer_question(self, question: str, topic: Optional[str] = None) -> str:
        """Use OpenAI to answer a question about the restaurant"""
        restaurant_name = self._restaurant_info.get('name', 'our restaurant')
        knowledge_base = self._restaurant_info.get('knowledge_base', '')
        
        system_prompt = f"""You are a helpful assistant for {restaurant_name}.
Answer the customer's question concisely and friendly.

Restaurant Info:
- Name: {restaurant_name}
- Address: {self._restaurant_info.get('address', '')}, {self._restaurant_info.get('city', '')}
- Phone: {self._restaurant_info.get('phone', '')}

{"Knowledge Base: " + knowledge_base if knowledge_base else ""}

If you don't know the answer, say so politely and offer to help with a reservation.
Keep responses brief."""

        try:
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
            return f"I apologize, I couldn't process your question. Would you like to make a reservation instead?"
    
    def _process_contextual_response(self, message: str, conv_state: ConversationState) -> Optional[Tuple[str, Any]]:
        """
        Process a response based on what we last asked the user.
        Returns (field_name, parsed_value) or None if not parseable.
        """
        last_question = conv_state.last_question
        print(f"Processing contextual response. Last question: {last_question}, Message: {message}", flush=True)
        
        if last_question == 'guests':
            # Try to parse guest count
            guests = parse_number_from_text(message)
            if guests:
                print(f"Parsed guests from context: {guests}", flush=True)
                return ('guests', guests)
        
        elif last_question == 'date':
            # Try to parse date
            date_str, date_display = parse_date_from_text(message)
            if date_str:
                print(f"Parsed date from context: {date_str}", flush=True)
                return ('date', (date_str, date_display))
        
        elif last_question == 'time':
            # Try to parse time
            time_str, time_display = parse_time_from_text(message)
            if time_str:
                print(f"Parsed time from context: {time_str}", flush=True)
                return ('time', (time_str, time_display))
        
        return None
    
    def chat_sync(self, message: str, session_id: Optional[str] = None,
                  conversation_history: List[Dict] = None,
                  sender_name: Optional[str] = None,
                  sender_phone: Optional[str] = None) -> str:
        """
        Process user message and return response.
        Uses OpenAI for intelligent entity extraction.
        State is persisted to database between messages.
        """
        self._get_restaurant_info()
        restaurant_name = self._restaurant_info.get('name', 'our restaurant')
        
        # Update customer info
        if sender_name:
            self._customer_info['name'] = sender_name
        if sender_phone:
            self._customer_info['phone'] = sender_phone
        
        # Load state from database (KEY CHANGE!)
        conv_state = None
        if session_id:
            conv_state = load_conversation_state(self.restaurant_id, session_id, self.app)
        
        # If no saved state, create new one
        if not conv_state:
            conv_state = ConversationState(
                restaurant_id=self.restaurant_id,
                timezone=self._timezone
            )
        
        booking = conv_state.booking_details
        
        # Update customer info in state
        if sender_name and not conv_state.incoming_customer_name:
            conv_state.incoming_customer_name = sender_name
        if sender_phone and not conv_state.incoming_customer_phone:
            conv_state.incoming_customer_phone = sender_phone
        
        message_lower = message.lower().strip()
        
        print(f"=== AI ASSISTANT ===", flush=True)
        print(f"Session ID: {session_id}", flush=True)
        print(f"State: {conv_state.state}", flush=True)
        print(f"Last question: {conv_state.last_question}", flush=True)
        print(f"Message: {message}", flush=True)
        print(f"Booking so far: date={booking.date}, time={booking.time}, guests={booking.guests}", flush=True)
        
        # Retrieve memories for context (if available)
        memory_context = ""
        if self._memory_service and self._memory_service.is_available and sender_phone:
            try:
                # Get relevant memories for this customer
                memories = self._memory_service.search_memories(
                    query=message,
                    phone=sender_phone,
                    restaurant_id=self.restaurant_id,
                    limit=5
                )
                if memories:
                    memory_context = format_memories_for_context(memories)
                    print(f"Retrieved {len(memories)} memories for context", flush=True)
                
                # Also get customer context (name, preferences, etc.)
                customer_context = self._memory_service.get_customer_context(
                    phone=sender_phone,
                    restaurant_id=self.restaurant_id
                )
                if customer_context.customer_name and not conv_state.incoming_customer_name:
                    conv_state.incoming_customer_name = customer_context.customer_name
                    print(f"Retrieved customer name from memory: {customer_context.customer_name}", flush=True)
            except Exception as e:
                print(f"Error retrieving memories: {e}", flush=True)
        
        # Helper function to save state and return response
        def respond(response: AssistantResponse) -> str:
            if session_id:
                save_conversation_state(self.restaurant_id, session_id, response.conversation_state, self.app)
            
            # Store conversation in memory (async-safe)
            if self._memory_service and self._memory_service.is_available and sender_phone:
                try:
                    self._memory_service.add_conversation_memory(
                        user_message=message,
                        assistant_response=response.text,
                        phone=sender_phone,
                        restaurant_id=self.restaurant_id
                    )
                except Exception as e:
                    print(f"Error storing memory: {e}", flush=True)
            
            return response.model_dump_json()
        
        # =================================================================
        # FIRST: Try context-aware parsing for direct answers
        # =================================================================
        
        if conv_state.state in [BookingState.AWAITING_DATE, BookingState.AWAITING_TIME, 
                                BookingState.AWAITING_GUESTS, BookingState.COLLECTING_INFO]:
            contextual_result = self._process_contextual_response(message, conv_state)
            
            if contextual_result:
                field_name, value = contextual_result
                
                if field_name == 'guests':
                    booking.guests = value
                    print(f"Set guests from contextual parsing: {value}", flush=True)
                    
                    # Check for large party
                    if booking.guests > self.MAX_GUESTS_WITHOUT_HANDOVER:
                        booking.requires_human_handover = True
                        booking.handover_reason = f"Large party ({booking.guests} guests)"
                        conv_state.state = BookingState.HANDOVER_TO_HUMAN
                        conv_state.booking_details = booking
                        
                        customer_name = conv_state.incoming_customer_name or "Guest"
                        customer_phone = conv_state.incoming_customer_phone or "Not provided"
                        
                        return respond(AssistantResponse(
                            text=f"For {booking.guests} guests, our staff will assist you personally.\n\n"
                                 f"Your Request:\n"
                                 f"- Date: {booking.date_display or booking.date or 'TBD'}\n"
                                 f"- Time: {booking.time_display or booking.time or 'TBD'}\n"
                                 f"- Guests: {booking.guests}\n\n"
                                 f"Contact: {customer_name} ({customer_phone})\n\n"
                                 f"Our team will contact you within 24 hours. Any special requests?",
                            conversation_state=conv_state
                        ))
                
                elif field_name == 'date':
                    date_str, date_display = value
                    booking.date = date_str
                    booking.date_display = date_display
                    print(f"Set date from contextual parsing: {date_str}", flush=True)
                
                elif field_name == 'time':
                    time_str, time_display = value
                    booking.time = time_str
                    booking.time_display = time_display
                    print(f"Set time from contextual parsing: {time_str}", flush=True)
                
                conv_state.booking_details = booking
                conv_state.state = BookingState.COLLECTING_INFO
                
                # Check if all info is now complete
                if booking.is_complete():
                    customer_name = conv_state.incoming_customer_name
                    customer_phone = conv_state.incoming_customer_phone
                    
                    # Check if the name looks like a phone number (common Chatwoot issue)
                    name_is_valid = customer_name and not is_phone_number_like(customer_name)
                    
                    if name_is_valid and customer_phone:
                        # We have a valid name and phone - ask for confirmation
                        booking.customer_name = customer_name
                        booking.customer_phone = customer_phone
                        conv_state.state = BookingState.AWAITING_NAME_CONFIRMATION
                        conv_state.booking_details = booking
                        
                        return respond(AssistantResponse(
                            text=f"Great! I have your reservation:\n\n"
                                 f"Date: {booking.date_display or booking.date}\n"
                                 f"Time: {booking.time_display or booking.time}\n"
                                 f"Guests: {booking.guests}\n\n"
                                 f"Is this contact info correct?\n"
                                 f"Name: {customer_name}\n"
                                 f"Phone: {customer_phone}",
                            buttons=self._get_yes_no_buttons(),
                            button_type='confirm_contact',
                            conversation_state=conv_state
                        ))
                    else:
                        # Name is missing or looks like a phone number - ask for name
                        # Store the phone for later use
                        if customer_phone:
                            booking.customer_phone = customer_phone
                        conv_state.state = BookingState.AWAITING_NAME_INPUT
                        conv_state.booking_details = booking
                        return respond(AssistantResponse(
                            text=f"Great! {booking.date_display or booking.date} at {booking.time_display or booking.time} for {booking.guests}.\n\n"
                                 f"May I have your name for the reservation?",
                            conversation_state=conv_state
                        ))
                else:
                    # Ask for next missing info
                    response = self._ask_for_missing_info(conv_state, booking)
                    return respond(response)
        
        # =================================================================
        # HANDLE STATES THAT EXPECT SPECIFIC INPUT
        # =================================================================
        
        # --- AWAITING NAME CONFIRMATION ---
        if conv_state.state == BookingState.AWAITING_NAME_CONFIRMATION:
            if message_lower in ['yes', 'correct', 'y', 'confirm', 'that\'s right', 'thats right', 'si', 'ja']:
                conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                return respond(AssistantResponse(
                    text="Do you have any special requests?",
                    buttons=self._get_special_request_buttons(),
                    button_type='special_requests',
                    conversation_state=conv_state
                ))
            
            elif message_lower in ['no', 'wrong', 'n', 'update', 'change']:
                booking.customer_name = None
                booking.customer_phone = None
                conv_state.state = BookingState.AWAITING_NAME_INPUT
                conv_state.booking_details = booking
                return respond(AssistantResponse(
                    text="Please provide your name for the reservation:",
                    conversation_state=conv_state
                ))
            
            else:
                # User is providing their name
                if len(message) >= 2:
                    booking.customer_name = message.strip()
                    if conv_state.incoming_customer_phone:
                        booking.customer_phone = conv_state.incoming_customer_phone
                        conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                        conv_state.booking_details = booking
                        return respond(AssistantResponse(
                            text="Do you have any special requests?",
                            buttons=self._get_special_request_buttons(),
                            button_type='special_requests',
                            conversation_state=conv_state
                        ))
                    else:
                        conv_state.state = BookingState.AWAITING_PHONE_INPUT
                        conv_state.booking_details = booking
                        return respond(AssistantResponse(
                            text=f"Thanks {booking.customer_name.split()[0]}! What's your phone number?",
                            conversation_state=conv_state
                        ))
        
        # --- AWAITING NAME INPUT ---
        if conv_state.state == BookingState.AWAITING_NAME_INPUT:
            if len(message) >= 2:
                booking.customer_name = message.strip()
                if conv_state.incoming_customer_phone:
                    booking.customer_phone = conv_state.incoming_customer_phone
                    conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                    conv_state.booking_details = booking
                    return respond(AssistantResponse(
                        text="Do you have any special requests?",
                        buttons=self._get_special_request_buttons(),
                        button_type='special_requests',
                        conversation_state=conv_state
                    ))
                else:
                    conv_state.state = BookingState.AWAITING_PHONE_INPUT
                    conv_state.booking_details = booking
                    return respond(AssistantResponse(
                        text=f"Thanks {booking.customer_name.split()[0]}! What's your phone number?",
                        conversation_state=conv_state
                    ))
        
        # --- AWAITING PHONE INPUT ---
        if conv_state.state == BookingState.AWAITING_PHONE_INPUT:
            digits = ''.join(c for c in message if c.isdigit())
            if len(digits) >= 7:
                booking.customer_phone = message.strip()
                conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                conv_state.booking_details = booking
                return respond(AssistantResponse(
                    text="Do you have any special requests?",
                    buttons=self._get_special_request_buttons(),
                    button_type='special_requests',
                    conversation_state=conv_state
                ))
            else:
                return respond(AssistantResponse(
                    text="Please provide a valid phone number:",
                    conversation_state=conv_state
                ))
        
        # --- AWAITING SPECIAL REQUESTS ---
        if conv_state.state == BookingState.AWAITING_SPECIAL_REQUESTS:
            special_map = {
                'none': None,
                'window': 'Window seat preferred',
                'quiet': 'Quiet area preferred',
                'birthday': 'Birthday celebration',
                'anniversary': 'Anniversary celebration',
                'other': None
            }
            
            if message_lower in special_map:
                if message_lower == 'other':
                    return respond(AssistantResponse(
                        text="Please type your special request:",
                        conversation_state=conv_state
                    ))
                booking.special_requests = special_map[message_lower]
            else:
                booking.special_requests = message.strip() if message_lower != 'none' else None
            
            conv_state.state = BookingState.AWAITING_FINAL_CONFIRMATION
            conv_state.booking_details = booking
            
            summary = (
                f"Reservation Summary:\n\n"
                f"Date: {booking.date_display or booking.date}\n"
                f"Time: {booking.time_display or booking.time}\n"
                f"Guests: {booking.guests}\n"
                f"Name: {booking.customer_name}\n"
                f"Phone: {booking.customer_phone}\n"
            )
            if booking.special_requests:
                summary += f"Special requests: {booking.special_requests}\n"
            summary += f"\nRestaurant: {restaurant_name}\n\nConfirm reservation?"
            
            return respond(AssistantResponse(
                text=summary,
                buttons=self._get_confirm_buttons(),
                button_type='final_confirm',
                conversation_state=conv_state
            ))
        
        # --- AWAITING FINAL CONFIRMATION ---
        if conv_state.state == BookingState.AWAITING_FINAL_CONFIRMATION:
            # Check for confirmation - be flexible with matching
            confirm_words = ['confirm', 'yes', 'y', 'book', 'si', 'ja', 'ok', 'okay', 'sure', 'absolutely', 'definitely', 'great', 'perfect']
            cancel_words = ['cancel', 'no', 'n', 'stop', 'nevermind', 'never mind', 'abort']
            
            # Check if message starts with or contains confirmation words
            is_confirmation = any(
                message_lower == word or 
                message_lower.startswith(word + ' ') or 
                message_lower.startswith(word + ',') or
                message_lower.startswith(word + '!')
                for word in confirm_words
            )
            is_cancellation = any(word in message_lower for word in cancel_words)
            
            if is_confirmation and not is_cancellation:
                result = self._make_reservation(booking)
                
                if result['success']:
                    conv_state.state = BookingState.COMPLETED
                    # Clear state after successful booking
                    if session_id:
                        clear_conversation_state(self.restaurant_id, session_id, self.app)
                    
                    # Store reservation in memory for future reference
                    if self._memory_service and self._memory_service.is_available and sender_phone:
                        try:
                            self._memory_service.add_reservation_memory(
                                phone=sender_phone,
                                restaurant_id=self.restaurant_id,
                                reservation_details={
                                    'date': booking.date,
                                    'date_display': booking.date_display,
                                    'time': booking.time,
                                    'time_display': booking.time_display,
                                    'guests': booking.guests,
                                    'customer_name': booking.customer_name,
                                    'special_requests': booking.special_requests,
                                    'reservation_id': result['reservation_id'],
                                    'table_name': result['table_name']
                                }
                            )
                            # Also store customer name for future lookups
                            if booking.customer_name:
                                self._memory_service.add_preference(
                                    phone=sender_phone,
                                    restaurant_id=self.restaurant_id,
                                    preference=f"Customer name is {booking.customer_name}",
                                    category="identity"
                                )
                            # Store special requests as preferences
                            if booking.special_requests:
                                self._memory_service.add_preference(
                                    phone=sender_phone,
                                    restaurant_id=self.restaurant_id,
                                    preference=booking.special_requests,
                                    category="seating"
                                )
                            print(f"Stored reservation in memory for {sender_phone}", flush=True)
                        except Exception as e:
                            print(f"Error storing reservation in memory: {e}", flush=True)
                    
                    return respond(AssistantResponse(
                        text=f"Confirmed!\n\n"
                             f"Date: {booking.date_display or booking.date} at {booking.time_display or booking.time}\n"
                             f"Guests: {booking.guests}\n"
                             f"Table: {result['table_name']}\n"
                             f"Confirmation #: {result['reservation_id']}\n\n"
                             f"See you at {restaurant_name}!",
                        conversation_state=conv_state
                    ))
                else:
                    return respond(AssistantResponse(
                        text=f"Sorry, there was an issue: {result.get('error')}. Please try again.",
                        conversation_state=conv_state
                    ))
            
            elif is_cancellation:
                conv_state.state = BookingState.INITIAL
                conv_state.booking_details = BookingDetails()
                # Clear state on cancellation
                if session_id:
                    clear_conversation_state(self.restaurant_id, session_id, self.app)
                return respond(AssistantResponse(
                    text="Reservation cancelled. How else can I help?",
                    conversation_state=conv_state
                ))
            
            else:
                # User said something else - ask for clarification
                summary = (
                    f"Your reservation:\n\n"
                    f"📅 {booking.date_display or booking.date}\n"
                    f"🕐 {booking.time_display or booking.time}\n"
                    f"👥 {booking.guests} guests\n"
                    f"📛 {booking.customer_name}\n"
                )
                if booking.special_requests:
                    summary += f"📝 {booking.special_requests}\n"
                summary += f"\nPlease reply 'Yes' to confirm or 'No' to cancel."
                
                return respond(AssistantResponse(
                    text=summary,
                    buttons=self._get_confirm_buttons(),
                    button_type='final_confirm',
                    conversation_state=conv_state
                ))
        
        # --- HANDOVER TO HUMAN ---
        if conv_state.state == BookingState.HANDOVER_TO_HUMAN:
            if message.strip():
                if booking.special_requests:
                    booking.special_requests += f"\n{message}"
                else:
                    booking.special_requests = message
                conv_state.booking_details = booking
            
            conv_state.state = BookingState.COMPLETED
            return respond(AssistantResponse(
                text="Thank you! Our team will contact you within 24 hours.",
                conversation_state=conv_state
            ))
        
        # =================================================================
        # EXTRACT INFO FROM MESSAGE USING OPENAI (for complex messages)
        # =================================================================
        
        # Build context hint for OpenAI
        context_hint = None
        if conv_state.last_question == 'guests':
            context_hint = "How many guests will be dining?"
        elif conv_state.last_question == 'date':
            context_hint = "When would you like to dine?"
        elif conv_state.last_question == 'time':
            context_hint = "What time would you prefer?"
        
        extracted = self._extract_reservation_info(message, context_hint)
        print(f"Extracted: intent={extracted.has_reservation_intent}, date={extracted.date}, time={extracted.time}, guests={extracted.guests}", flush=True)
        
        # Handle questions
        if extracted.is_question and not extracted.has_reservation_intent:
            answer = self._answer_question(message, extracted.question_topic)
            return respond(AssistantResponse(
                text=answer,
                conversation_state=conv_state
            ))
        
        # =================================================================
        # COLLECTING INFO STATE - Process extracted data
        # =================================================================
        
        if conv_state.state in [BookingState.INITIAL, BookingState.COLLECTING_INFO,
                                BookingState.AWAITING_DATE, BookingState.AWAITING_TIME, 
                                BookingState.AWAITING_GUESTS]:
            # Update booking with any extracted info
            if extracted.date:
                booking.date = extracted.date
                booking.date_display = extracted.date_display
            if extracted.time:
                booking.time = extracted.time
                booking.time_display = extracted.time_display
            if extracted.guests:
                booking.guests = extracted.guests
            if extracted.name:
                booking.customer_name = extracted.name
            
            conv_state.booking_details = booking
            
            # Check if reservation intent detected
            if extracted.has_reservation_intent or conv_state.state != BookingState.INITIAL:
                conv_state.state = BookingState.COLLECTING_INFO
                
                # Check for large party
                if booking.guests and booking.guests > self.MAX_GUESTS_WITHOUT_HANDOVER:
                    booking.requires_human_handover = True
                    booking.handover_reason = f"Large party ({booking.guests} guests)"
                    conv_state.state = BookingState.HANDOVER_TO_HUMAN
                    conv_state.booking_details = booking
                    
                    customer_name = conv_state.incoming_customer_name or "Guest"
                    customer_phone = conv_state.incoming_customer_phone or "Not provided"
                    
                    return respond(AssistantResponse(
                        text=f"For {booking.guests} guests, our staff will assist you personally.\n\n"
                             f"Your Request:\n"
                             f"- Date: {booking.date_display or booking.date or 'TBD'}\n"
                             f"- Time: {booking.time_display or booking.time or 'TBD'}\n"
                             f"- Guests: {booking.guests}\n\n"
                             f"Contact: {customer_name} ({customer_phone})\n\n"
                             f"Our team will contact you within 24 hours. Any special requests?",
                        conversation_state=conv_state
                    ))
                
                # Check if all required info is collected
                if booking.is_complete():
                    # Move to name confirmation
                    customer_name = conv_state.incoming_customer_name
                    customer_phone = conv_state.incoming_customer_phone
                    
                    # Check if the name looks like a phone number (common Chatwoot issue)
                    name_is_valid = customer_name and not is_phone_number_like(customer_name)
                    
                    if name_is_valid and customer_phone:
                        # We have a valid name and phone - ask for confirmation
                        booking.customer_name = customer_name
                        booking.customer_phone = customer_phone
                        conv_state.state = BookingState.AWAITING_NAME_CONFIRMATION
                        conv_state.booking_details = booking
                        
                        return respond(AssistantResponse(
                            text=f"Great! I have your reservation:\n\n"
                                 f"Date: {booking.date_display or booking.date}\n"
                                 f"Time: {booking.time_display or booking.time}\n"
                                 f"Guests: {booking.guests}\n\n"
                                 f"Is this contact info correct?\n"
                                 f"Name: {customer_name}\n"
                                 f"Phone: {customer_phone}",
                            buttons=self._get_yes_no_buttons(),
                            button_type='confirm_contact',
                            conversation_state=conv_state
                        ))
                    else:
                        # Name is missing or looks like a phone number - ask for name
                        # Store the phone for later use
                        if customer_phone:
                            booking.customer_phone = customer_phone
                        conv_state.state = BookingState.AWAITING_NAME_INPUT
                        conv_state.booking_details = booking
                        return respond(AssistantResponse(
                            text=f"Great! {booking.date_display or booking.date} at {booking.time_display or booking.time} for {booking.guests}.\n\n"
                                 f"May I have your name for the reservation?",
                            conversation_state=conv_state
                        ))
                else:
                    # Ask for missing info
                    response = self._ask_for_missing_info(conv_state, booking)
                    return respond(response)
        
        # =================================================================
        # DEFAULT: Welcome message
        # =================================================================
        
        return respond(AssistantResponse(
            text=f"Welcome to {restaurant_name}!\n\n"
                 "I can help you make a reservation. Just tell me:\n"
                 "- When you'd like to dine\n"
                 "- What time\n"
                 "- How many guests\n\n"
                 "For example: \"Table for 4 tomorrow at 7pm\"",
            conversation_state=conv_state
        ))


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def get_assistant(restaurant_id: int, app=None, 
                  customer_name: Optional[str] = None,
                  customer_phone: Optional[str] = None) -> ReservationAssistant:
    """Get a reservation assistant for a restaurant."""
    customer_info = {}
    if customer_name:
        customer_info['name'] = customer_name
    if customer_phone:
        customer_info['phone'] = customer_phone
    
    return ReservationAssistant(restaurant_id, app, customer_info)
