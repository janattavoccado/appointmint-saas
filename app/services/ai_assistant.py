"""
AI Reservation Assistant Service with OpenAI-Based Entity Extraction
Uses Pydantic models for structured data and OpenAI for intelligent understanding.

This module uses:
1. OpenAI for intent detection and entity extraction from natural language
2. Pydantic models for structured state management
3. A conversational flow that only asks for missing information

Reservation Flow:
1. Extract all available info from user message using OpenAI
2. Ask only for missing required fields
3. Confirm name/phone from incoming message
4. Collect special requests
5. Final confirmation and database storage
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
    COLLECTING_INFO = 'collecting_info'
    AWAITING_NAME_CONFIRMATION = 'awaiting_name_confirmation'
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
# RESERVATION ASSISTANT CLASS
# =============================================================================

class ReservationAssistant:
    """AI-powered reservation assistant with OpenAI-based entity extraction"""
    
    MAX_GUESTS_WITHOUT_HANDOVER = 8
    
    def __init__(self, restaurant_id: int, app=None, customer_info: Optional[Dict] = None):
        self.restaurant_id = restaurant_id
        self.app = app
        self._restaurant_info = None
        self._timezone = 'UTC'
        self._client = OpenAI()
        self._customer_info = customer_info or {}
    
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
    
    def _extract_reservation_info(self, message: str) -> ExtractedReservationInfo:
        """
        Use OpenAI to extract reservation information from user message.
        This is the key function that understands natural language.
        """
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        tomorrow_str = (today + timedelta(days=1)).strftime('%Y-%m-%d')
        
        system_prompt = f"""You are an AI that extracts reservation information from user messages.
Today's date is {today_str} ({today.strftime('%A')}).
Tomorrow is {tomorrow_str}.

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
6. guests: Number of people (look for "for 4", "party of 2", "2 people", etc.)
7. name: Customer name if explicitly mentioned
8. is_question: true if user is asking a question about the restaurant (hours, menu, location, etc.)
9. question_topic: What the question is about if is_question is true

IMPORTANT: 
- "dine" means reservation intent
- "die" is likely a transcription error for "dine"
- Be generous in detecting reservation intent
- Extract ALL available information from the message

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
            ButtonOption(value='yes', display='✓ Yes, correct'),
            ButtonOption(value='no', display='✗ No, update')
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
    
    def _restore_state(self, conversation_history: List[Dict]) -> ConversationState:
        """Restore conversation state from history"""
        if conversation_history:
            for msg in reversed(conversation_history):
                if msg.get('role') == 'assistant' and msg.get('conversation_state'):
                    try:
                        return ConversationState.model_validate(msg['conversation_state'])
                    except Exception:
                        pass
        
        state = ConversationState(
            restaurant_id=self.restaurant_id,
            timezone=self._timezone
        )
        
        if self._customer_info:
            state.incoming_customer_name = self._customer_info.get('name')
            state.incoming_customer_phone = self._customer_info.get('phone')
        
        return state
    
    def _ask_for_missing_info(self, conv_state: ConversationState, booking: BookingDetails) -> AssistantResponse:
        """Generate response asking for the next missing piece of information"""
        missing = booking.get_missing_fields()
        
        if 'date' in missing:
            return AssistantResponse(
                text="When would you like to dine? You can type a date or select below:",
                buttons=self._get_next_7_dates(),
                button_type='date',
                conversation_state=conv_state
            )
        elif 'time' in missing:
            date_text = booking.date_display or booking.date
            return AssistantResponse(
                text=f"Great! {date_text}. What time would you prefer?",
                buttons=self._get_time_buttons(),
                button_type='time',
                conversation_state=conv_state
            )
        elif 'guests' in missing:
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
    
    def chat_sync(self, message: str, session_id: Optional[str] = None,
                  conversation_history: List[Dict] = None,
                  sender_name: Optional[str] = None,
                  sender_phone: Optional[str] = None) -> str:
        """
        Process user message and return response.
        Uses OpenAI for intelligent entity extraction.
        """
        self._get_restaurant_info()
        restaurant_name = self._restaurant_info.get('name', 'our restaurant')
        
        # Update customer info
        if sender_name:
            self._customer_info['name'] = sender_name
        if sender_phone:
            self._customer_info['phone'] = sender_phone
        
        # Restore state
        conv_state = self._restore_state(conversation_history or [])
        booking = conv_state.booking_details
        
        # Update customer info in state
        if sender_name and not conv_state.incoming_customer_name:
            conv_state.incoming_customer_name = sender_name
        if sender_phone and not conv_state.incoming_customer_phone:
            conv_state.incoming_customer_phone = sender_phone
        
        message_lower = message.lower().strip()
        
        print(f"=== AI ASSISTANT ===", flush=True)
        print(f"State: {conv_state.state}", flush=True)
        print(f"Message: {message}", flush=True)
        print(f"Booking so far: date={booking.date}, time={booking.time}, guests={booking.guests}", flush=True)
        
        # =================================================================
        # HANDLE STATES THAT EXPECT SPECIFIC INPUT
        # =================================================================
        
        # --- AWAITING NAME CONFIRMATION ---
        if conv_state.state == BookingState.AWAITING_NAME_CONFIRMATION:
            if message_lower in ['yes', 'correct', 'y', 'confirm', 'that\'s right', 'thats right']:
                conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                response = AssistantResponse(
                    text="Do you have any special requests?",
                    buttons=self._get_special_request_buttons(),
                    button_type='special_requests',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            
            elif message_lower in ['no', 'wrong', 'n', 'update', 'change']:
                booking.customer_name = None
                booking.customer_phone = None
                conv_state.booking_details = booking
                response = AssistantResponse(
                    text="Please provide your name for the reservation:",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            
            else:
                # User is providing their name
                if len(message) >= 2:
                    booking.customer_name = message.strip()
                    if conv_state.incoming_customer_phone:
                        booking.customer_phone = conv_state.incoming_customer_phone
                        conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                        conv_state.booking_details = booking
                        response = AssistantResponse(
                            text="Do you have any special requests?",
                            buttons=self._get_special_request_buttons(),
                            button_type='special_requests',
                            conversation_state=conv_state
                        )
                        return response.model_dump_json()
                    else:
                        conv_state.booking_details = booking
                        response = AssistantResponse(
                            text=f"Thanks {booking.customer_name.split()[0]}! What's your phone number?",
                            conversation_state=conv_state
                        )
                        return response.model_dump_json()
                
                # Check if it's a phone number
                digits = ''.join(c for c in message if c.isdigit())
                if len(digits) >= 7:
                    booking.customer_phone = message.strip()
                    conv_state.state = BookingState.AWAITING_SPECIAL_REQUESTS
                    conv_state.booking_details = booking
                    response = AssistantResponse(
                        text="Do you have any special requests?",
                        buttons=self._get_special_request_buttons(),
                        button_type='special_requests',
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
        
        # --- AWAITING SPECIAL REQUESTS ---
        elif conv_state.state == BookingState.AWAITING_SPECIAL_REQUESTS:
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
                    response = AssistantResponse(
                        text="Please type your special request:",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
                booking.special_requests = special_map[message_lower]
            else:
                booking.special_requests = message.strip() if message_lower != 'none' else None
            
            conv_state.state = BookingState.AWAITING_FINAL_CONFIRMATION
            conv_state.booking_details = booking
            
            summary = (
                f"📋 **Reservation Summary**\n\n"
                f"📅 {booking.date_display or booking.date}\n"
                f"🕐 {booking.time_display or booking.time}\n"
                f"👥 {booking.guests} guest{'s' if booking.guests > 1 else ''}\n"
                f"👤 {booking.customer_name}\n"
                f"📞 {booking.customer_phone}\n"
            )
            if booking.special_requests:
                summary += f"📝 {booking.special_requests}\n"
            summary += f"\n🍽️ {restaurant_name}\n\nConfirm reservation?"
            
            response = AssistantResponse(
                text=summary,
                buttons=self._get_confirm_buttons(),
                button_type='final_confirm',
                conversation_state=conv_state
            )
            return response.model_dump_json()
        
        # --- AWAITING FINAL CONFIRMATION ---
        elif conv_state.state == BookingState.AWAITING_FINAL_CONFIRMATION:
            if message_lower in ['confirm', 'yes', 'y', 'book']:
                result = self._make_reservation(booking)
                
                if result['success']:
                    conv_state.state = BookingState.COMPLETED
                    response = AssistantResponse(
                        text=f"🎉 **Confirmed!**\n\n"
                             f"📅 {booking.date_display} at {booking.time_display}\n"
                             f"👥 {booking.guests} guests\n"
                             f"📍 {result['table_name']}\n"
                             f"🎫 #{result['reservation_id']}\n\n"
                             f"See you at {restaurant_name}!",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
                else:
                    response = AssistantResponse(
                        text=f"Sorry, there was an issue: {result.get('error')}. Please try again.",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
            
            elif message_lower in ['cancel', 'no', 'n']:
                conv_state.state = BookingState.INITIAL
                conv_state.booking_details = BookingDetails()
                response = AssistantResponse(
                    text="Reservation cancelled. How else can I help?",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        # --- HANDOVER TO HUMAN ---
        elif conv_state.state == BookingState.HANDOVER_TO_HUMAN:
            if message.strip():
                if booking.special_requests:
                    booking.special_requests += f"\n{message}"
                else:
                    booking.special_requests = message
                conv_state.booking_details = booking
            
            conv_state.state = BookingState.COMPLETED
            response = AssistantResponse(
                text="Thank you! Our team will contact you within 24 hours.",
                conversation_state=conv_state
            )
            return response.model_dump_json()
        
        # =================================================================
        # EXTRACT INFO FROM MESSAGE USING OPENAI
        # =================================================================
        
        extracted = self._extract_reservation_info(message)
        print(f"Extracted: intent={extracted.has_reservation_intent}, date={extracted.date}, time={extracted.time}, guests={extracted.guests}", flush=True)
        
        # Handle questions
        if extracted.is_question and not extracted.has_reservation_intent:
            answer = self._answer_question(message, extracted.question_topic)
            response = AssistantResponse(
                text=answer,
                conversation_state=conv_state
            )
            return response.model_dump_json()
        
        # =================================================================
        # COLLECTING INFO STATE - Process extracted data
        # =================================================================
        
        if conv_state.state in [BookingState.INITIAL, BookingState.COLLECTING_INFO]:
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
            if extracted.has_reservation_intent or conv_state.state == BookingState.COLLECTING_INFO:
                conv_state.state = BookingState.COLLECTING_INFO
                
                # Check for large party
                if booking.guests and booking.guests > self.MAX_GUESTS_WITHOUT_HANDOVER:
                    booking.requires_human_handover = True
                    booking.handover_reason = f"Large party ({booking.guests} guests)"
                    conv_state.state = BookingState.HANDOVER_TO_HUMAN
                    conv_state.booking_details = booking
                    
                    customer_name = conv_state.incoming_customer_name or "Guest"
                    customer_phone = conv_state.incoming_customer_phone or "Not provided"
                    
                    response = AssistantResponse(
                        text=f"For {booking.guests} guests, our staff will assist you personally.\n\n"
                             f"📋 **Your Request:**\n"
                             f"• Date: {booking.date_display or booking.date or 'TBD'}\n"
                             f"• Time: {booking.time_display or booking.time or 'TBD'}\n"
                             f"• Guests: {booking.guests}\n\n"
                             f"📞 **Contact:** {customer_name} ({customer_phone})\n\n"
                             f"Our team will contact you within 24 hours. Any special requests?",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
                
                # Check if all required info is collected
                if booking.is_complete():
                    # Move to name confirmation
                    customer_name = conv_state.incoming_customer_name
                    customer_phone = conv_state.incoming_customer_phone
                    
                    if customer_name and customer_phone:
                        booking.customer_name = customer_name
                        booking.customer_phone = customer_phone
                        conv_state.state = BookingState.AWAITING_NAME_CONFIRMATION
                        conv_state.booking_details = booking
                        
                        response = AssistantResponse(
                            text=f"Great! I have your reservation:\n\n"
                                 f"📅 {booking.date_display}\n"
                                 f"🕐 {booking.time_display}\n"
                                 f"👥 {booking.guests} guests\n\n"
                                 f"Is this contact info correct?\n"
                                 f"👤 {customer_name}\n"
                                 f"📞 {customer_phone}",
                            buttons=self._get_yes_no_buttons(),
                            button_type='confirm_contact',
                            conversation_state=conv_state
                        )
                        return response.model_dump_json()
                    else:
                        conv_state.state = BookingState.AWAITING_NAME_CONFIRMATION
                        conv_state.booking_details = booking
                        response = AssistantResponse(
                            text=f"Great! {booking.date_display} at {booking.time_display} for {booking.guests}.\n\n"
                                 f"May I have your name for the reservation?",
                            conversation_state=conv_state
                        )
                        return response.model_dump_json()
                else:
                    # Ask for missing info
                    conv_state.booking_details = booking
                    return self._ask_for_missing_info(conv_state, booking).model_dump_json()
        
        # =================================================================
        # DEFAULT: Welcome message
        # =================================================================
        
        response = AssistantResponse(
            text=f"Welcome to {restaurant_name}! 👋\n\n"
                 "I can help you make a reservation. Just tell me:\n"
                 "• When you'd like to dine\n"
                 "• What time\n"
                 "• How many guests\n\n"
                 "For example: \"Table for 4 tomorrow at 7pm\"",
            conversation_state=conv_state
        )
        return response.model_dump_json()


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
