"""
AI Reservation Assistant Service with Button-Based Flow and Pydantic State Management
Supports both text and voice interactions for restaurant table reservations.

This module uses Pydantic models for structured state management and
a state-machine approach with interactive buttons for a guided reservation flow.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum
from pydantic import BaseModel, Field
from openai import OpenAI

from app.models import db, Restaurant, Table, Reservation, AIConversation, Tenant
from app.services.datetime_utils import (
    get_current_datetime,
    parse_datetime,
    parse_relative_date,
    parse_time
)


class BookingState(str, Enum):
    """Enum for booking conversation states"""
    INITIAL = 'initial'
    AWAITING_DATE = 'awaiting_date'
    AWAITING_TIME = 'awaiting_time'
    AWAITING_GUESTS = 'awaiting_guests'
    AWAITING_NAME = 'awaiting_name'
    AWAITING_PHONE = 'awaiting_phone'
    AWAITING_CONFIRMATION = 'awaiting_confirmation'
    SPECIAL_REQUEST = 'special_request'
    COMPLETED = 'completed'


class BookingDetails(BaseModel):
    """Pydantic model for booking details"""
    date: Optional[str] = Field(None, description="Reservation date in YYYY-MM-DD format")
    date_display: Optional[str] = Field(None, description="Human-readable date display")
    time: Optional[str] = Field(None, description="Reservation time in HH:MM format")
    time_display: Optional[str] = Field(None, description="Human-readable time display (12-hour)")
    guests: Optional[int] = Field(None, description="Number of guests", ge=1, le=20)
    name: Optional[str] = Field(None, description="Customer name")
    phone: Optional[str] = Field(None, description="Customer phone number")
    email: Optional[str] = Field(None, description="Customer email (optional)")
    special_requests: Optional[str] = Field(None, description="Special requests")


class ConversationState(BaseModel):
    """Pydantic model for conversation state - persisted between requests"""
    state: BookingState = Field(default=BookingState.INITIAL, description="Current booking state")
    booking_details: BookingDetails = Field(default_factory=BookingDetails, description="Collected booking details")
    restaurant_id: int = Field(..., description="Restaurant ID")
    timezone: str = Field(default='UTC', description="Restaurant timezone")


class ButtonOption(BaseModel):
    """Pydantic model for a button option"""
    value: str = Field(..., description="Value sent when button is clicked")
    display: str = Field(..., description="Display text for the button")


class AssistantResponse(BaseModel):
    """Pydantic model for assistant response"""
    text: str = Field(..., description="Response text message")
    buttons: Optional[List[ButtonOption]] = Field(None, description="Optional buttons to display")
    button_type: Optional[str] = Field(None, description="Type of buttons: date, guests, confirm")
    conversation_state: ConversationState = Field(..., description="Current conversation state to persist")


class ReservationAssistant:
    """AI-powered reservation assistant with Pydantic state management"""
    
    def __init__(self, restaurant_id: int, app=None):
        self.restaurant_id = restaurant_id
        self.app = app
        self._restaurant_info = None
        self._timezone = 'UTC'
        self._client = OpenAI()
    
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
    
    def _get_next_5_dates(self) -> List[ButtonOption]:
        """Get the next 5 available dates starting from today"""
        datetime_info = get_current_datetime(self._timezone)
        today = datetime.strptime(datetime_info['current_date'], '%Y-%m-%d')
        
        dates = []
        for i in range(5):
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
    
    def _get_guest_buttons(self) -> List[ButtonOption]:
        """Get guest count buttons 1-8 plus special request option"""
        buttons = []
        for i in range(1, 9):
            buttons.append(ButtonOption(
                value=str(i),
                display=f"{i} guest{'s' if i > 1 else ''}"
            ))
        buttons.append(ButtonOption(
            value='9+',
            display='9+ guests (special request)'
        ))
        return buttons
    
    def _get_confirm_buttons(self) -> List[ButtonOption]:
        """Get confirmation buttons"""
        return [
            ButtonOption(value='confirm', display='✓ Confirm Booking'),
            ButtonOption(value='cancel', display='✗ Cancel')
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
                    return {'success': False, 'error': 'No tables available'}
                
                # Create reservation
                reservation = Reservation(
                    restaurant_id=self.restaurant_id,
                    table_id=selected_table.id,
                    customer_name=booking.name,
                    customer_phone=booking.phone,
                    customer_email=booking.email or '',
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
                    if not tenant.is_paid and tenant.trial_booking_count is not None:
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
        # Look for the most recent state in conversation history
        if conversation_history:
            for msg in reversed(conversation_history):
                if msg.get('role') == 'assistant' and msg.get('conversation_state'):
                    try:
                        return ConversationState.model_validate(msg['conversation_state'])
                    except Exception:
                        pass
        
        # Return fresh state if none found
        return ConversationState(
            restaurant_id=self.restaurant_id,
            timezone=self._timezone
        )
    
    def chat_sync(self, message: str, session_id: Optional[str] = None,
                  conversation_history: List[Dict] = None) -> str:
        """
        Process user message and return response with buttons.
        Uses Pydantic models for state management.
        
        Args:
            message: The user's message
            session_id: Optional session ID for conversation continuity
            conversation_history: Previous conversation messages with state
        
        Returns:
            JSON string with response, buttons, and conversation state
        """
        # Initialize restaurant info and timezone
        self._get_restaurant_info()
        restaurant_name = self._restaurant_info.get('name', 'our restaurant')
        
        # Restore state from conversation history
        conv_state = self._restore_state(conversation_history or [])
        booking = conv_state.booking_details
        
        message_lower = message.lower().strip()
        
        # Process based on current state
        if conv_state.state == BookingState.AWAITING_DATE:
            # Check if it's a date value (YYYY-MM-DD format)
            if len(message) == 10 and message[4] == '-' and message[7] == '-':
                booking.date = message
                date_obj = datetime.strptime(message, '%Y-%m-%d')
                booking.date_display = date_obj.strftime('%A, %B %d, %Y')
                conv_state.state = BookingState.AWAITING_TIME
                conv_state.booking_details = booking
                
                response = AssistantResponse(
                    text=f"Great choice! What time would you like to dine on {booking.date_display}?",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            else:
                # Try to parse natural language date
                parsed = parse_relative_date(message, self._timezone)
                if parsed:
                    booking.date = parsed.strftime('%Y-%m-%d')
                    booking.date_display = parsed.strftime('%A, %B %d, %Y')
                    conv_state.state = BookingState.AWAITING_TIME
                    conv_state.booking_details = booking
                    
                    response = AssistantResponse(
                        text=f"Great! What time would you like to dine on {booking.date_display}?",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
        
        elif conv_state.state == BookingState.AWAITING_TIME:
            # Try to parse time
            parsed_time = parse_time(message)
            if parsed_time:
                hour, minute = parsed_time
                booking.time = f"{hour:02d}:{minute:02d}"
                # Format 12-hour display
                period = 'AM' if hour < 12 else 'PM'
                display_hour = hour % 12 or 12
                booking.time_display = f"{display_hour}:{minute:02d} {period}" if minute else f"{display_hour} {period}"
                conv_state.state = BookingState.AWAITING_GUESTS
                conv_state.booking_details = booking
                
                response = AssistantResponse(
                    text="How many guests will be dining with you?",
                    buttons=self._get_guest_buttons(),
                    button_type='guests',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        elif conv_state.state == BookingState.AWAITING_GUESTS:
            # Handle guest count
            if message == '9+':
                conv_state.state = BookingState.SPECIAL_REQUEST
                conv_state.booking_details = booking
                
                response = AssistantResponse(
                    text="For parties of 9 or more, we'll need to check availability with our manager. "
                         "Please provide your name, and our staff will contact you within 24 hours to confirm your reservation.",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            
            try:
                guests = int(message.replace(' guests', '').replace(' guest', '').strip())
                if 1 <= guests <= 8:
                    booking.guests = guests
                    conv_state.state = BookingState.AWAITING_NAME
                    conv_state.booking_details = booking
                    
                    response = AssistantResponse(
                        text="May I have your name for the reservation?",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
            except ValueError:
                pass
        
        elif conv_state.state == BookingState.AWAITING_NAME:
            if len(message) >= 2:
                booking.name = message.strip()
                conv_state.state = BookingState.AWAITING_PHONE
                conv_state.booking_details = booking
                
                first_name = booking.name.split()[0]
                response = AssistantResponse(
                    text=f"Thanks, {first_name}. What's the best phone number to reach you?",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        elif conv_state.state == BookingState.AWAITING_PHONE:
            # Basic phone validation - at least some digits
            digits = ''.join(c for c in message if c.isdigit())
            if len(digits) >= 7:
                booking.phone = message.strip()
                conv_state.state = BookingState.AWAITING_CONFIRMATION
                conv_state.booking_details = booking
                
                summary = (f"Please confirm your reservation:\n\n"
                          f"📅 Date: {booking.date_display}\n"
                          f"🕐 Time: {booking.time_display}\n"
                          f"👥 Guests: {booking.guests}\n"
                          f"👤 Name: {booking.name}\n"
                          f"📞 Phone: {booking.phone}")
                
                response = AssistantResponse(
                    text=summary,
                    buttons=self._get_confirm_buttons(),
                    button_type='confirm',
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        elif conv_state.state == BookingState.AWAITING_CONFIRMATION:
            if 'confirm' in message_lower or 'yes' in message_lower or message_lower == 'confirm':
                # Make the reservation
                result = self._make_reservation(booking)
                if result['success']:
                    conv_state.state = BookingState.COMPLETED
                    conv_state.booking_details = booking
                    
                    response = AssistantResponse(
                        text=f"🎉 Your reservation is confirmed!\n\n"
                             f"📅 {booking.date_display} at {booking.time_display}\n"
                             f"👥 {booking.guests} guests\n"
                             f"📍 {result['table_name']} ({result['table_location']})\n"
                             f"🎫 Confirmation #{result['reservation_id']}\n\n"
                             f"We look forward to seeing you at {restaurant_name}!",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
                else:
                    response = AssistantResponse(
                        text=f"I'm sorry, there was an issue: {result.get('error', 'Unknown error')}. "
                             "Please try again or contact the restaurant directly.",
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
            
            elif 'cancel' in message_lower or 'no' in message_lower:
                # Reset state
                conv_state.state = BookingState.INITIAL
                conv_state.booking_details = BookingDetails()
                
                response = AssistantResponse(
                    text="No problem! Your reservation has been cancelled. Is there anything else I can help you with?",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        elif conv_state.state == BookingState.SPECIAL_REQUEST:
            # For 9+ guests, collect name and phone
            if not booking.name:
                booking.name = message.strip()
                conv_state.booking_details = booking
                
                first_name = booking.name.split()[0]
                response = AssistantResponse(
                    text=f"Thank you, {first_name}. What's the best phone number to reach you?",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
            elif not booking.phone:
                booking.phone = message.strip()
                conv_state.state = BookingState.COMPLETED
                conv_state.booking_details = booking
                
                response = AssistantResponse(
                    text=f"Thank you! We've noted your request for a large party on {booking.date_display or 'your preferred date'}. "
                         f"Our staff will contact you at {booking.phone} within 24 hours to confirm availability.",
                    conversation_state=conv_state
                )
                return response.model_dump_json()
        
        # Initial state or reservation intent detected
        if conv_state.state == BookingState.INITIAL or 'reserv' in message_lower or 'book' in message_lower or 'table' in message_lower:
            conv_state.state = BookingState.AWAITING_DATE
            conv_state.booking_details = BookingDetails()  # Reset booking details
            
            response = AssistantResponse(
                text="Great! Please select a date for your reservation:",
                buttons=self._get_next_5_dates(),
                button_type='date',
                conversation_state=conv_state
            )
            return response.model_dump_json()
        
        # Check if this is a question that can be answered from knowledge base
        knowledge_base = self._restaurant_info.get('knowledge_base', '')
        if knowledge_base and conv_state.state == BookingState.INITIAL:
            # Check for common question keywords
            question_keywords = ['menu', 'hour', 'open', 'close', 'location', 'address', 'park', 
                               'vegetarian', 'vegan', 'gluten', 'allerg', 'price', 'cost',
                               'dress', 'code', 'private', 'event', 'catering', 'takeout',
                               'delivery', 'outdoor', 'patio', 'wheelchair', 'accessible',
                               'happy hour', 'special', 'wine', 'drink', 'dessert', 'appetizer',
                               'what do you', 'do you have', 'can i', 'is there', 'where']
            
            is_question = any(kw in message_lower for kw in question_keywords) or '?' in message
            
            if is_question:
                # Use OpenAI to answer from knowledge base
                answer = self._answer_from_knowledge_base(message, knowledge_base)
                if answer:
                    response = AssistantResponse(
                        text=answer,
                        conversation_state=conv_state
                    )
                    return response.model_dump_json()
        
        # Default response for other queries
        response = AssistantResponse(
            text=f"Welcome to {restaurant_name}! I'm your AI reservation assistant. "
                 "I can help you make a reservation, check availability, or answer questions about the restaurant. "
                 "How can I help you today?",
            conversation_state=conv_state
        )
        return response.model_dump_json()
    
    def _answer_from_knowledge_base(self, question: str, knowledge_base: str) -> Optional[str]:
        """
        Use OpenAI to answer a question based on the restaurant's knowledge base.
        Returns None if no relevant answer can be found.
        """
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
            
            answer = response.choices[0].message.content.strip()
            return answer
            
        except Exception as e:
            # If OpenAI fails, return None to fall back to default response
            return None


# Factory function
def get_assistant(restaurant_id: int, app=None) -> ReservationAssistant:
    """
    Get a reservation assistant for a restaurant.
    Always creates a new instance (state is managed via conversation_history).
    """
    return ReservationAssistant(restaurant_id, app)
