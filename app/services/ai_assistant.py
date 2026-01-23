"""
AI Assistant for Restaurant Reservations
Based on conversation-history approach that maintains full context.
Integrates with Chatwoot/WhatsApp and persists conversation history in database.
"""

from flask import Flask
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import pytz
import os
import json

# Import database models
from app.models import Restaurant, Reservation, AIConversation, db


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class TableReservation(BaseModel):
    """Validated table reservation data"""
    date: str = Field(..., description="Reservation date in YYYY-MM-DD format")
    time: str = Field(..., description="Reservation time in HH:MM format (24-hour)")
    guests: int = Field(..., description="Number of guests (1-8)")
    name: str = Field(..., description="Customer name")
    phone: str = Field(..., description="Customer phone number")
    special_requests: Optional[str] = Field(None, description="Any special requests")

    class Config:
        json_schema_extra = {
            "example": {
                "date": "2026-01-25",
                "time": "19:00",
                "guests": 4,
                "name": "John Doe",
                "phone": "+46 73 123 4567",
                "special_requests": "Window seat preferred"
            }
        }


class ConversationMessage(BaseModel):
    """A single message in the conversation"""
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class AssistantResponse(BaseModel):
    """Response from the AI assistant"""
    text: str = Field(..., description="Response text to send to user")
    reservation: Optional[Dict[str, Any]] = Field(None, description="Completed reservation data if any")
    conversation_cleared: bool = Field(False, description="Whether conversation was cleared")


# =============================================================================
# CONVERSATION HISTORY MANAGEMENT (Database-backed)
# =============================================================================

def get_conversation_history(restaurant_id: int, conversation_id: str, app: Flask) -> List[Dict[str, str]]:
    """
    Get conversation history from database.
    
    Args:
        restaurant_id: Restaurant ID
        conversation_id: Chatwoot conversation ID
        app: Flask app for database context
    
    Returns:
        List of message dicts with 'role' and 'content'
    """
    with app.app_context():
        try:
            # Look for existing conversation history
            record = AIConversation.query.filter_by(
                restaurant_id=restaurant_id,
                conversation_type=f"history_{conversation_id}"
            ).first()
            
            if record and record.transcript:
                try:
                    history = json.loads(record.transcript)
                    if isinstance(history, list):
                        return history
                except json.JSONDecodeError:
                    pass
            
            return []
        except Exception as e:
            print(f"Error loading conversation history: {e}", flush=True)
            return []


def save_conversation_history(restaurant_id: int, conversation_id: str, history: List[Dict[str, str]], app: Flask):
    """
    Save conversation history to database.
    
    Args:
        restaurant_id: Restaurant ID
        conversation_id: Chatwoot conversation ID
        history: List of message dicts
        app: Flask app for database context
    """
    with app.app_context():
        try:
            record = AIConversation.query.filter_by(
                restaurant_id=restaurant_id,
                conversation_type=f"history_{conversation_id}"
            ).first()
            
            if record:
                record.transcript = json.dumps(history)
            else:
                # Create new record - don't set created_at/updated_at as they may be auto-managed
                record = AIConversation(
                    restaurant_id=restaurant_id,
                    conversation_type=f"history_{conversation_id}",
                    transcript=json.dumps(history)
                )
                db.session.add(record)
            
            db.session.commit()
            print(f"Saved conversation history for {conversation_id}: {len(history)} messages", flush=True)
        except Exception as e:
            print(f"Error saving conversation history: {e}", flush=True)
            import traceback
            traceback.print_exc()
            db.session.rollback()


def clear_conversation_history(restaurant_id: int, conversation_id: str, app: Flask):
    """Clear conversation history from database."""
    with app.app_context():
        try:
            AIConversation.query.filter_by(
                restaurant_id=restaurant_id,
                conversation_type=f"history_{conversation_id}"
            ).delete()
            db.session.commit()
            print(f"Cleared conversation history for {conversation_id}", flush=True)
        except Exception as e:
            print(f"Error clearing conversation history: {e}", flush=True)
            db.session.rollback()


def add_to_conversation_history(
    restaurant_id: int, 
    conversation_id: str, 
    role: str, 
    content: str, 
    app: Flask,
    max_messages: int = 20
):
    """
    Add a message to conversation history.
    
    Args:
        restaurant_id: Restaurant ID
        conversation_id: Chatwoot conversation ID
        role: 'user' or 'assistant'
        content: Message content
        app: Flask app for database context
        max_messages: Maximum messages to keep (default 20)
    """
    history = get_conversation_history(restaurant_id, conversation_id, app)
    
    history.append({
        "role": role,
        "content": content
    })
    
    # Keep only last N messages to avoid token limits
    if len(history) > max_messages:
        history = history[-max_messages:]
    
    save_conversation_history(restaurant_id, conversation_id, history, app)


# =============================================================================
# RESERVATION ASSISTANT CLASS
# =============================================================================

class ReservationAssistant:
    """
    AI Assistant for handling restaurant reservations.
    Maintains full conversation history for context.
    """
    
    def __init__(self, restaurant_id: int, app: Flask):
        """
        Initialize the assistant.
        
        Args:
            restaurant_id: Restaurant ID
            app: Flask app for database context
        """
        self.restaurant_id = restaurant_id
        self.app = app
        self.openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
        
        # Initialize mem0 if available
        self._mem0_client = None
        try:
            mem0_key = os.environ.get('MEM0_API_KEY')
            if mem0_key:
                from mem0 import MemoryClient
                self._mem0_client = MemoryClient(api_key=mem0_key)
                print("Mem0 client initialized", flush=True)
        except Exception as e:
            print(f"Mem0 not available: {e}", flush=True)
        
        # Load restaurant info
        self._restaurant = None
        self._knowledgebase = None
        self._timezone = pytz.timezone('Europe/Stockholm')  # Default timezone
        
        self._load_restaurant_info()
    
    def _load_restaurant_info(self):
        """Load restaurant information from database."""
        with self.app.app_context():
            try:
                self._restaurant = Restaurant.query.get(self.restaurant_id)
                if self._restaurant:
                    self._knowledgebase = self._restaurant.knowledge_base or ""
                    # Use restaurant timezone if available
                    if hasattr(self._restaurant, 'timezone') and self._restaurant.timezone:
                        try:
                            self._timezone = pytz.timezone(self._restaurant.timezone)
                        except:
                            pass
                    print(f"Loaded restaurant: {self._restaurant.name}", flush=True)
            except Exception as e:
                print(f"Error loading restaurant: {e}", flush=True)
    
    def _get_current_datetime_info(self) -> Dict[str, str]:
        """Get current date and time information."""
        now = datetime.now(self._timezone)
        tomorrow = now + timedelta(days=1)
        
        return {
            "current_datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "current_date": now.strftime("%Y-%m-%d"),
            "current_time": now.strftime("%H:%M"),
            "day_of_week": now.strftime("%A"),
            "tomorrow_date": tomorrow.strftime("%Y-%m-%d"),
            "timezone": str(self._timezone)
        }
    
    def _create_system_prompt(self, customer_name: Optional[str] = None, customer_phone: Optional[str] = None) -> str:
        """Create the system prompt with current context."""
        datetime_info = self._get_current_datetime_info()
        restaurant_name = self._restaurant.name if self._restaurant else "Our Restaurant"
        restaurant_phone = getattr(self._restaurant, 'phone', 'our staff') if self._restaurant else 'our staff'
        
        # Customer context
        customer_context = ""
        if customer_name and customer_name != customer_phone:
            customer_context = f"\nKNOWN CUSTOMER INFORMATION:\n- Name: {customer_name}\n"
        if customer_phone:
            customer_context += f"- Phone: {customer_phone}\n"
        
        return f"""You are a friendly and professional table reservation assistant for {restaurant_name}.

CURRENT DATE AND TIME INFORMATION:
- Current Date: {datetime_info['current_date']}
- Current Time: {datetime_info['current_time']}
- Day of Week: {datetime_info['day_of_week']}
- Tomorrow: {datetime_info['tomorrow_date']}
- Timezone: {datetime_info['timezone']}

IMPORTANT DATE UNDERSTANDING:
- "Today" means: {datetime_info['current_date']}
- "Tonight" means: {datetime_info['current_date']} (evening time)
- "Tomorrow" means: {datetime_info['tomorrow_date']}
- For weekday names (Monday, Tuesday, etc.), calculate the nearest FUTURE date for that weekday
- Support date formats: YYYY-MM-DD, DD/MM/YYYY, "January 25", "25th", etc.
{customer_context}
RESTAURANT INFORMATION:
{self._knowledgebase or "No specific restaurant information available."}

YOUR RESPONSIBILITIES:
1. Answer questions about the restaurant using the information above
2. Help customers make table reservations by collecting:
   - Date (understand "today", "tomorrow", "tonight", weekdays, various formats)
   - Time (accept 12-hour with AM/PM and 24-hour format, convert to 24-hour HH:MM)
   - Number of guests (if more than 8, inform them to contact staff directly at {restaurant_phone})
   - Customer name (use the known name if available, or ask)
   - Phone number (use the known phone if available, or ask)
   - Any special requests (optional)

CRITICAL: MAINTAIN CONVERSATION CONTEXT
- You are having a CONTINUOUS conversation with the customer
- REMEMBER all information they have already provided in this conversation
- DO NOT ask for information they have already given you
- Keep track of what you have collected: date, time, guests, name, phone, special requests
- Only ask for information you don't have yet
- When the customer provides additional details, ADD them to what you already know
- If the customer's name and phone are already known (shown above), use them and just confirm

3. When you have collected ALL required information (date, time, guests, name, phone), respond with a JSON object in this EXACT format:
{{
  "reservation_complete": true,
  "data": {{
    "date": "YYYY-MM-DD",
    "time": "HH:MM",
    "guests": number,
    "name": "customer name",
    "phone": "phone number",
    "special_requests": "requests or null"
  }}
}}

4. Before completing the reservation, ALWAYS confirm all details with the customer in a summary.

5. Be conversational, friendly, and helpful. Guide the conversation naturally.

RESPONSE GUIDELINES:
- Always respond in plain text for conversation
- Only use the JSON format when ALL reservation details are confirmed and complete
- Be natural and friendly, don't sound robotic
- If more than 8 guests, politely say: "For parties larger than 8 guests, please contact our staff directly at {restaurant_phone}"
- When confirming details, list everything clearly
- Keep responses concise but helpful
"""
    
    def _get_memory_context(self, user_id: str, query: str) -> str:
        """Get relevant memories from mem0."""
        if not self._mem0_client:
            return ""
        
        try:
            # mem0 v2 API requires filters parameter
            memories = self._mem0_client.search(
                query, 
                filters={"user_id": user_id}, 
                limit=5
            )
            if memories and memories.get('results'):
                memory_texts = [m.get('memory', '') for m in memories['results'] if m.get('memory')]
                if memory_texts:
                    return "\n".join([f"- {m}" for m in memory_texts])
        except Exception as e:
            print(f"Error getting memories: {e}", flush=True)
        
        return ""
    
    def _store_memory(self, user_id: str, user_message: str, assistant_response: str):
        """Store conversation in mem0."""
        if not self._mem0_client:
            return
        
        try:
            self._mem0_client.add(
                messages=[
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_response}
                ],
                user_id=user_id
            )
            print(f"Stored memory for user {user_id}", flush=True)
        except Exception as e:
            print(f"Error storing memory: {e}", flush=True)
    
    def _save_reservation(self, reservation_data: Dict[str, Any], customer_phone: str) -> bool:
        """Save reservation to database."""
        with self.app.app_context():
            try:
                # Parse date and time
                res_date = datetime.strptime(reservation_data['date'], '%Y-%m-%d').date()
                res_time = datetime.strptime(reservation_data['time'], '%H:%M').time()
                
                reservation = Reservation(
                    restaurant_id=self.restaurant_id,
                    customer_name=reservation_data['name'],
                    customer_phone=reservation_data.get('phone', customer_phone),
                    customer_email=None,
                    date=res_date,
                    time=res_time,
                    party_size=reservation_data['guests'],
                    special_requests=reservation_data.get('special_requests'),
                    status='confirmed',
                    source='whatsapp',
                    created_at=datetime.utcnow()
                )
                
                db.session.add(reservation)
                db.session.commit()
                
                print(f"Reservation saved: {reservation.id}", flush=True)
                return True
            except Exception as e:
                print(f"Error saving reservation: {e}", flush=True)
                db.session.rollback()
                return False
    
    def chat(
        self,
        message: str,
        conversation_id: str,
        sender_name: Optional[str] = None,
        sender_phone: Optional[str] = None
    ) -> AssistantResponse:
        """
        Process a chat message and return a response.
        
        Args:
            message: User's message
            conversation_id: Chatwoot conversation ID for history tracking
            sender_name: Customer name from Chatwoot
            sender_phone: Customer phone from Chatwoot
        
        Returns:
            AssistantResponse with text and optional reservation data
        """
        print(f"\n{'='*60}", flush=True)
        print(f"=== RESERVATION ASSISTANT ===", flush=True)
        print(f"Conversation ID: {conversation_id}", flush=True)
        print(f"Message: {message}", flush=True)
        print(f"Sender: {sender_name} / {sender_phone}", flush=True)
        
        # Generate user ID for mem0
        user_id = f"restaurant_{self.restaurant_id}_user_{sender_phone or conversation_id}"
        
        # Get conversation history from database
        conversation_history = get_conversation_history(self.restaurant_id, conversation_id, self.app)
        print(f"Loaded {len(conversation_history)} previous messages", flush=True)
        
        # Get memory context from mem0
        memory_context = self._get_memory_context(user_id, message)
        
        # Create system prompt
        system_prompt = self._create_system_prompt(sender_name, sender_phone)
        
        # Add memory context if available
        if memory_context:
            system_prompt += f"\n\nRELEVANT CUSTOMER HISTORY FROM PREVIOUS VISITS:\n{memory_context}"
        
        # Build messages for OpenAI
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history
        messages.extend(conversation_history)
        
        # Add current user message
        messages.append({"role": "user", "content": message})
        
        print(f"Sending {len(messages)} messages to OpenAI", flush=True)
        
        # Get response from OpenAI
        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.7,
                max_tokens=1000
            )
            
            assistant_message = response.choices[0].message.content
            print(f"OpenAI response: {assistant_message[:200]}...", flush=True)
        except Exception as e:
            print(f"OpenAI error: {e}", flush=True)
            return AssistantResponse(
                text="I'm sorry, I'm having trouble processing your request. Please try again.",
                reservation=None,
                conversation_cleared=False
            )
        
        # Add messages to conversation history
        add_to_conversation_history(self.restaurant_id, conversation_id, "user", message, self.app)
        add_to_conversation_history(self.restaurant_id, conversation_id, "assistant", assistant_message, self.app)
        
        # Store in mem0
        self._store_memory(user_id, message, assistant_message)
        
        # Check if this is a completed reservation
        reservation_data = None
        conversation_cleared = False
        final_response = assistant_message
        
        if "reservation_complete" in assistant_message:
            try:
                # Extract JSON from response
                json_start = assistant_message.find('{')
                json_end = assistant_message.rfind('}') + 1
                
                if json_start != -1 and json_end > json_start:
                    json_str = assistant_message[json_start:json_end]
                    parsed = json.loads(json_str)
                    
                    if parsed.get('reservation_complete'):
                        # Validate with Pydantic
                        reservation = TableReservation(**parsed['data'])
                        reservation_data = reservation.model_dump()
                        
                        # Save to database
                        self._save_reservation(reservation_data, sender_phone or '')
                        
                        # Generate confirmation message
                        special_req_text = f"\n📝 Special Requests: {reservation_data['special_requests']}" if reservation_data.get('special_requests') else ""
                        
                        final_response = f"""✅ Reservation Confirmed!

📅 Date: {reservation_data['date']}
🕐 Time: {reservation_data['time']}
👥 Guests: {reservation_data['guests']}
👤 Name: {reservation_data['name']}
📞 Phone: {reservation_data['phone']}{special_req_text}

Thank you for your reservation! We look forward to welcoming you. You will receive a confirmation shortly."""
                        
                        # Clear conversation history after successful reservation
                        clear_conversation_history(self.restaurant_id, conversation_id, self.app)
                        conversation_cleared = True
                        
                        print(f"Reservation completed and saved!", flush=True)
                        
            except (json.JSONDecodeError, ValidationError, KeyError) as e:
                print(f"Failed to parse reservation JSON: {e}", flush=True)
        
        return AssistantResponse(
            text=final_response,
            reservation=reservation_data,
            conversation_cleared=conversation_cleared
        )
    
    def chat_sync(
        self,
        message: str,
        session_id: str,
        conversation_history: List[Dict] = None,
        sender_name: Optional[str] = None,
        sender_phone: Optional[str] = None
    ) -> str:
        """
        Synchronous chat method for compatibility with existing webhook.
        Returns JSON string response.
        
        Args:
            message: User's message
            session_id: Chatwoot conversation ID
            conversation_history: Ignored (we use database)
            sender_name: Customer name
            sender_phone: Customer phone
        
        Returns:
            JSON string with response
        """
        response = self.chat(
            message=message,
            conversation_id=session_id,
            sender_name=sender_name,
            sender_phone=sender_phone
        )
        
        # Return in format expected by webhook
        return json.dumps({
            "text": response.text,
            "buttons": None,
            "button_type": None,
            "reservation": response.reservation
        })


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def get_assistant(restaurant_id: int, app: Flask) -> ReservationAssistant:
    """
    Get or create a ReservationAssistant for a restaurant.
    
    Args:
        restaurant_id: Restaurant ID
        app: Flask app for database context
    
    Returns:
        ReservationAssistant instance
    """
    return ReservationAssistant(restaurant_id, app)
