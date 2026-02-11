"""
AI Assistant for Restaurant Reservations
Based on conversation-history approach that maintains full context.
Integrates with Chatwoot/WhatsApp and persists conversation history in database.
Supports interactive buttons for confirmations.
Includes smart table assignment based on floor plan availability.
"""

from flask import Flask
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError
from datetime import datetime, timedelta, date, time
from typing import Optional, List, Dict, Any, Tuple
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


class TableAvailabilityResult(BaseModel):
    """Result of table availability check"""
    available: bool = Field(..., description="Whether a suitable table was found")
    table_config_id: Optional[int] = Field(None, description="TableConfig database ID")
    table_id: Optional[str] = Field(None, description="Table identifier (e.g., T1, T2)")
    table_name: Optional[str] = Field(None, description="Table name if set")
    seats: Optional[int] = Field(None, description="Number of seats at the table")
    table_type: Optional[str] = Field(None, description="Table type (standard, booth, etc.)")
    current_status: Optional[str] = Field(None, description="Current table status")
    next_reservation_at: Optional[str] = Field(None, description="Next reservation time on this table")
    minutes_until_next: Optional[int] = Field(None, description="Minutes until next reservation")
    reason: Optional[str] = Field(None, description="Reason if no table available")


class InteractiveButton(BaseModel):
    """A button for interactive messages"""
    title: str = Field(..., description="Button display text")
    value: str = Field(..., description="Value sent when button is clicked")


class ConversationMessage(BaseModel):
    """A single message in the conversation"""
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class AssistantResponse(BaseModel):
    """Response from the AI assistant"""
    text: str = Field(..., description="Response text to send to user")
    reservation: Optional[Dict[str, Any]] = Field(None, description="Completed reservation data if any")
    conversation_cleared: bool = Field(False, description="Whether conversation was cleared")
    buttons: Optional[List[InteractiveButton]] = Field(None, description="Interactive buttons to display")
    needs_confirmation: bool = Field(False, description="Whether this is a confirmation request")


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
                conversation_type=f"h_{conversation_id}"[:20]
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
                conversation_type=f"h_{conversation_id}"[:20]
            ).first()
            
            if record:
                record.transcript = json.dumps(history)
            else:
                # Create new record - don't set created_at/updated_at as they may be auto-managed
                record = AIConversation(
                    restaurant_id=restaurant_id,
                    conversation_type=f"h_{conversation_id}"[:20],
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
                conversation_type=f"h_{conversation_id}"[:20]
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
# TABLE AVAILABILITY TOOL
# =============================================================================

def find_available_table(
    restaurant_id: int,
    reservation_date: str,
    reservation_time: str,
    party_size: int,
    duration_minutes: int = 90,
    app: Flask = None
) -> TableAvailabilityResult:
    """
    Find the best available table for a reservation.
    
    Logic:
    1. Get all tables from the restaurant's active floor plan
    2. Filter for tables with status 'free' or 'completed' (needs cleaning)
    3. Filter for tables with enough seats (seats >= party_size)
    4. Check existing reservations on the requested date for each table
    5. Ensure at least 90 minutes gap before the next reservation
    6. Return the best match (smallest suitable table to optimize seating)
    
    Args:
        restaurant_id: Restaurant ID
        reservation_date: Date in YYYY-MM-DD format
        reservation_time: Time in HH:MM format (24-hour)
        party_size: Number of guests
        duration_minutes: Duration of the reservation in minutes (default 90)
        app: Flask app for database context
    
    Returns:
        TableAvailabilityResult with the best available table or reason for unavailability
    """
    from app.models import FloorPlan, TableConfig
    
    try:
        # Parse the requested date and time
        req_date = datetime.strptime(reservation_date, '%Y-%m-%d').date()
        req_time = datetime.strptime(reservation_time, '%H:%M').time()
        req_datetime = datetime.combine(req_date, req_time)
        req_end_datetime = req_datetime + timedelta(minutes=duration_minutes)
        
        print(f"\n{'='*60}", flush=True)
        print(f"=== TABLE AVAILABILITY CHECK ===", flush=True)
        print(f"Restaurant: {restaurant_id}", flush=True)
        print(f"Date: {reservation_date}, Time: {reservation_time}", flush=True)
        print(f"Party size: {party_size}, Duration: {duration_minutes} min", flush=True)
        print(f"Requested slot: {req_datetime} - {req_end_datetime}", flush=True)
        
        # Get the active floor plan for this restaurant
        floor_plan = FloorPlan.query.filter_by(
            restaurant_id=restaurant_id,
            is_active=True
        ).first()
        
        if not floor_plan:
            print("No active floor plan found", flush=True)
            return TableAvailabilityResult(
                available=False,
                reason="No floor plan configured for this restaurant"
            )
        
        # Get all active tables from the floor plan
        all_tables = TableConfig.query.filter_by(
            floor_plan_id=floor_plan.id,
            is_active=True
        ).all()
        
        if not all_tables:
            print("No tables found in floor plan", flush=True)
            return TableAvailabilityResult(
                available=False,
                reason="No tables configured in the floor plan"
            )
        
        print(f"Found {len(all_tables)} active tables in floor plan", flush=True)
        
        # Filter tables by capacity (seats >= party_size)
        suitable_tables = [t for t in all_tables if t.seats >= party_size]
        
        if not suitable_tables:
            max_seats = max(t.seats for t in all_tables)
            print(f"No tables with enough seats. Max capacity: {max_seats}", flush=True)
            return TableAvailabilityResult(
                available=False,
                reason=f"No tables available with {party_size} or more seats. Maximum table capacity is {max_seats} seats."
            )
        
        print(f"{len(suitable_tables)} tables have enough seats", flush=True)
        
        # Filter tables by current status (free or completed/needs cleaning)
        available_status_tables = [
            t for t in suitable_tables 
            if (t.current_status or 'free') in ('free', 'completed')
        ]
        
        if not available_status_tables:
            print("No tables with free/completed status", flush=True)
            return TableAvailabilityResult(
                available=False,
                reason=f"All suitable tables are currently occupied or reserved. Please try a different time."
            )
        
        print(f"{len(available_status_tables)} tables are free or need cleaning", flush=True)
        
        # Get all reservations for this restaurant on the requested date
        day_reservations = Reservation.query.filter(
            Reservation.restaurant_id == restaurant_id,
            Reservation.reservation_date == req_date,
            Reservation.status.in_(['pending', 'confirmed'])
        ).all()
        
        print(f"Found {len(day_reservations)} reservations on {reservation_date}", flush=True)
        
        # Check each available table for time conflicts
        candidates = []
        
        for table in available_status_tables:
            # Find reservations linked to this specific table
            # Match by table_id (T1, T2, etc.) or by table_config linked reservation
            table_reservations = []
            
            for res in day_reservations:
                # Check if this reservation is linked to this table
                # via the table_id field or via current_reservation_id
                is_linked = False
                
                # Check by table_id (the old Table model ID)
                if res.table_id:
                    # Try to match table_id with the table_config's table_id string
                    # The Reservation.table_id references the old Table model
                    # We need to check if there's a mapping
                    pass
                
                # Check by current_reservation_id on the table config
                if table.current_reservation_id == res.id:
                    is_linked = True
                
                # Also check by matching table name/number patterns
                # This handles cases where reservations reference the table by name
                if res.table_id:
                    # Try to find the old Table model and match by table_number
                    from app.models import Table
                    old_table = Table.query.get(res.table_id)
                    if old_table and old_table.table_number == table.table_id:
                        is_linked = True
                
                if is_linked:
                    table_reservations.append(res)
            
            # Check time conflicts with existing reservations on this table
            has_conflict = False
            next_reservation_time = None
            minutes_until_next = None
            
            for res in table_reservations:
                res_start = datetime.combine(req_date, res.reservation_time)
                res_duration = res.duration_minutes or 90
                res_end = res_start + timedelta(minutes=res_duration)
                
                # Check if the requested slot overlaps with this reservation
                # We need at least 90 minutes gap
                # Requested: req_datetime to req_end_datetime
                # Existing: res_start to res_end
                
                # Conflict if: requested start < existing end AND requested end > existing start
                # But we also need 90 min buffer, so:
                # No conflict if: req_end_datetime <= res_start (with no overlap)
                # AND req_datetime >= res_end (with no overlap)
                
                if req_datetime < res_end and req_end_datetime > res_start:
                    # Direct overlap
                    has_conflict = True
                    print(f"  Table {table.table_id}: CONFLICT with reservation {res.id} ({res_start} - {res_end})", flush=True)
                    break
                
                # Check if there's at least 90 min gap before next reservation
                if res_start > req_datetime:
                    gap_minutes = (res_start - req_datetime).total_seconds() / 60
                    if gap_minutes < 90:
                        has_conflict = True
                        print(f"  Table {table.table_id}: Only {gap_minutes:.0f} min gap before reservation at {res_start}", flush=True)
                        break
                    
                    # Track the next reservation for info
                    if next_reservation_time is None or res_start < datetime.combine(req_date, datetime.strptime(next_reservation_time, '%H:%M').time()):
                        next_reservation_time = res.reservation_time.strftime('%H:%M')
                        minutes_until_next = int(gap_minutes)
            
            if not has_conflict:
                candidates.append({
                    'table': table,
                    'next_reservation_at': next_reservation_time,
                    'minutes_until_next': minutes_until_next
                })
                print(f"  Table {table.table_id}: AVAILABLE (seats: {table.seats}, status: {table.current_status})", flush=True)
            
        if not candidates:
            print("No tables available after time conflict check", flush=True)
            return TableAvailabilityResult(
                available=False,
                reason=f"No tables available at {reservation_time} on {reservation_date} with enough time before the next reservation. Please try a different time."
            )
        
        # Sort candidates: prefer smallest table that fits (optimize seating)
        # Secondary sort: prefer 'free' over 'completed' (needs cleaning)
        candidates.sort(key=lambda c: (
            0 if (c['table'].current_status or 'free') == 'free' else 1,  # free first
            c['table'].seats,  # smallest table first
        ))
        
        best = candidates[0]
        best_table = best['table']
        
        print(f"\n=== BEST TABLE: {best_table.table_id} ({best_table.seats} seats, status: {best_table.current_status}) ===", flush=True)
        
        return TableAvailabilityResult(
            available=True,
            table_config_id=best_table.id,
            table_id=best_table.table_id,
            table_name=best_table.table_name,
            seats=best_table.seats,
            table_type=best_table.table_type,
            current_status=best_table.current_status or 'free',
            next_reservation_at=best['next_reservation_at'],
            minutes_until_next=best['minutes_until_next'],
            reason=None
        )
        
    except Exception as e:
        print(f"Error in find_available_table: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return TableAvailabilityResult(
            available=False,
            reason=f"Error checking table availability: {str(e)}"
        )


def assign_table_for_reservation(
    table_config_id: int,
    reservation_id: int,
    guest_name: str,
    party_size: int,
    app: Flask = None
) -> bool:
    """
    Assign a table to a reservation and update the table's status to 'reserved'.
    
    Args:
        table_config_id: TableConfig database ID
        reservation_id: Reservation database ID
        guest_name: Guest name to display on the floor plan
        party_size: Number of guests
        app: Flask app for database context
    
    Returns:
        True if successful, False otherwise
    """
    from app.models import TableConfig
    
    try:
        table = TableConfig.query.get(table_config_id)
        if not table:
            print(f"Table config {table_config_id} not found", flush=True)
            return False
        
        # Update table status
        table.current_status = 'reserved'
        table.current_guest_name = guest_name
        table.current_guest_count = party_size
        table.current_reservation_id = reservation_id
        table.status_updated_at = datetime.utcnow()
        table.status_notes = f"Auto-assigned by booking agent"
        
        db.session.commit()
        print(f"Table {table.table_id} assigned to reservation {reservation_id} for {guest_name}", flush=True)
        return True
        
    except Exception as e:
        print(f"Error assigning table: {e}", flush=True)
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return False


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
    
    def _check_table_availability(self, reservation_date: str, reservation_time: str, party_size: int) -> str:
        """
        Check table availability and return a human-readable summary.
        This is the @tool that the booking agent uses.
        
        Args:
            reservation_date: Date in YYYY-MM-DD format
            reservation_time: Time in HH:MM format
            party_size: Number of guests
        
        Returns:
            String summary of availability for the AI to use in its response
        """
        result = find_available_table(
            restaurant_id=self.restaurant_id,
            reservation_date=reservation_date,
            reservation_time=reservation_time,
            party_size=party_size,
            app=self.app
        )
        
        if result.available:
            table_desc = result.table_id
            if result.table_name:
                table_desc = f"{result.table_id} ({result.table_name})"
            
            summary = f"TABLE AVAILABLE: {table_desc} with {result.seats} seats"
            if result.table_type and result.table_type != 'standard':
                summary += f" ({result.table_type})"
            if result.current_status == 'completed':
                summary += " [currently needs cleaning - will be ready]"
            if result.next_reservation_at:
                summary += f". Next reservation on this table at {result.next_reservation_at} ({result.minutes_until_next} min from requested time)"
            
            return summary
        else:
            return f"NO TABLE AVAILABLE: {result.reason}"
    
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

SMART TABLE ASSIGNMENT:
- When you have collected the date, time, and number of guests, a table availability check will be performed automatically
- The system will find the best available table based on:
  * Tables that are currently free or need cleaning (will be ready)
  * Tables with enough seats for the party
  * Tables with at least 90 minutes before the next reservation
- If a TABLE AVAILABLE message appears in the conversation, include the assigned table info in the confirmation
- If NO TABLE AVAILABLE, inform the customer and suggest alternative times
- You do NOT need to ask the customer which table they want - the system assigns the best one automatically

CRITICAL: MAINTAIN CONVERSATION CONTEXT
- You are having a CONTINUOUS conversation with the customer
- REMEMBER all information they have already provided in this conversation
- DO NOT ask for information they have already given you
- Keep track of what you have collected: date, time, guests, name, phone, special requests
- Only ask for information you don't have yet
- When the customer provides additional details, ADD them to what you already know
- If the customer's name and phone are already known (shown above), use them and just confirm

CONFIRMATION PROCESS:
When you have collected ALL required information (date, time, guests, name, phone) AND a table is available, you MUST:
1. First, show a summary and ask for confirmation with this EXACT format:

[CONFIRMATION_NEEDED]
📋 Reservation Summary:
📅 Date: [date]
🕐 Time: [time]
👥 Guests: [number]
👤 Name: [name]
📞 Phone: [phone]
🪑 Table: [table info from availability check]
📝 Special Requests: [requests or "None"]

Please confirm your reservation:
[END_CONFIRMATION]

2. Wait for the customer to confirm (they will say "yes", "confirm", etc. or click a button)

3. ONLY after confirmation, respond with the JSON object:
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

RESPONSE GUIDELINES:
- Always respond in plain text for conversation
- Use the [CONFIRMATION_NEEDED]...[END_CONFIRMATION] format when asking for final confirmation
- Only use the JSON format AFTER the customer confirms
- Be natural and friendly, don't sound robotic
- If more than 8 guests, politely say: "For parties larger than 8 guests, please contact our staff directly at {restaurant_phone}"
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
    
    def _extract_booking_details(self, conversation_history: List[Dict], current_message: str) -> Optional[Dict]:
        """
        Try to extract date, time, and party size from the conversation so far
        to perform an early table availability check.
        
        Returns dict with 'date', 'time', 'guests' if all three are found, else None.
        """
        # Build the full conversation text
        full_text = ""
        for msg in conversation_history:
            full_text += f"\n{msg['role']}: {msg['content']}"
        full_text += f"\nuser: {current_message}"
        
        # Use a simple LLM call to extract structured data
        try:
            extraction_response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": """Extract reservation details from the conversation. 
Return ONLY a JSON object with these fields (use null if not mentioned):
{"date": "YYYY-MM-DD or null", "time": "HH:MM or null", "guests": number or null}

Important:
- Convert relative dates (today, tomorrow, etc.) to actual dates
- Convert 12-hour times to 24-hour format
- Only extract information explicitly stated by the user"""},
                    {"role": "user", "content": full_text}
                ],
                temperature=0,
                max_tokens=100
            )
            
            result_text = extraction_response.choices[0].message.content.strip()
            # Clean up potential markdown formatting
            if result_text.startswith('```'):
                result_text = result_text.split('\n', 1)[1] if '\n' in result_text else result_text[3:]
                result_text = result_text.rsplit('```', 1)[0]
            
            extracted = json.loads(result_text)
            
            if extracted.get('date') and extracted.get('time') and extracted.get('guests'):
                return extracted
            
        except Exception as e:
            print(f"Error extracting booking details: {e}", flush=True)
        
        return None
    
    def _save_reservation(self, reservation_data: Dict[str, Any], customer_phone: str, table_availability: Optional[TableAvailabilityResult] = None) -> bool:
        """
        Save reservation to database and assign table if available.
        
        Args:
            reservation_data: Reservation details
            customer_phone: Customer phone number
            table_availability: Pre-checked table availability result
        """
        with self.app.app_context():
            try:
                # Parse date and time
                res_date = datetime.strptime(reservation_data['date'], '%Y-%m-%d').date()
                res_time = datetime.strptime(reservation_data['time'], '%H:%M').time()
                
                # Use correct field names for Reservation model
                reservation = Reservation(
                    restaurant_id=self.restaurant_id,
                    customer_name=reservation_data['name'],
                    customer_phone=reservation_data.get('phone', customer_phone),
                    customer_email=None,
                    reservation_date=res_date,
                    reservation_time=res_time,
                    party_size=reservation_data['guests'],
                    special_requests=reservation_data.get('special_requests'),
                    status='confirmed',
                    source='whatsapp'
                )
                
                db.session.add(reservation)
                db.session.commit()
                
                print(f"Reservation saved: {reservation.id}", flush=True)
                
                # If we have a table availability result, assign the table
                if table_availability and table_availability.available and table_availability.table_config_id:
                    assign_table_for_reservation(
                        table_config_id=table_availability.table_config_id,
                        reservation_id=reservation.id,
                        guest_name=reservation_data['name'],
                        party_size=reservation_data['guests'],
                        app=self.app
                    )
                else:
                    # Try to find a table now if we don't have one pre-checked
                    availability = find_available_table(
                        restaurant_id=self.restaurant_id,
                        reservation_date=reservation_data['date'],
                        reservation_time=reservation_data['time'],
                        party_size=reservation_data['guests'],
                        app=self.app
                    )
                    if availability.available and availability.table_config_id:
                        assign_table_for_reservation(
                            table_config_id=availability.table_config_id,
                            reservation_id=reservation.id,
                            guest_name=reservation_data['name'],
                            party_size=reservation_data['guests'],
                            app=self.app
                        )
                
                return True
            except Exception as e:
                print(f"Error saving reservation: {e}", flush=True)
                import traceback
                traceback.print_exc()
                db.session.rollback()
                return False
    
    def _check_for_confirmation_request(self, message: str) -> tuple[bool, str]:
        """
        Check if the AI response contains a confirmation request.
        Returns (needs_confirmation, cleaned_message)
        """
        if "[CONFIRMATION_NEEDED]" in message and "[END_CONFIRMATION]" in message:
            # Extract the confirmation message
            start = message.find("[CONFIRMATION_NEEDED]")
            end = message.find("[END_CONFIRMATION]") + len("[END_CONFIRMATION]")
            
            # Get the confirmation content without markers
            confirmation_content = message[start:end]
            confirmation_content = confirmation_content.replace("[CONFIRMATION_NEEDED]", "").replace("[END_CONFIRMATION]", "").strip()
            
            return True, confirmation_content
        
        return False, message
    
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
        
        # =====================================================================
        # TABLE AVAILABILITY CHECK
        # Try to extract date, time, guests from conversation to check availability
        # =====================================================================
        table_availability = None
        availability_context = ""
        
        try:
            booking_details = self._extract_booking_details(conversation_history, message)
            if booking_details:
                print(f"Extracted booking details: {booking_details}", flush=True)
                
                # Check table availability
                availability_summary = self._check_table_availability(
                    reservation_date=booking_details['date'],
                    reservation_time=booking_details['time'],
                    party_size=booking_details['guests']
                )
                
                # Store the full result for later use when saving
                table_availability = find_available_table(
                    restaurant_id=self.restaurant_id,
                    reservation_date=booking_details['date'],
                    reservation_time=booking_details['time'],
                    party_size=booking_details['guests'],
                    app=self.app
                )
                
                availability_context = f"\n\n[TABLE AVAILABILITY CHECK RESULT]\n{availability_summary}\n[END TABLE AVAILABILITY]"
                print(f"Table availability: {availability_summary}", flush=True)
        except Exception as e:
            print(f"Error during table availability check: {e}", flush=True)
        
        # Build messages for OpenAI
        messages = [{"role": "system", "content": system_prompt + availability_context}]
        
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
        buttons = None
        needs_confirmation = False
        
        # Check for confirmation request
        needs_confirmation, cleaned_message = self._check_for_confirmation_request(assistant_message)
        if needs_confirmation:
            final_response = cleaned_message
            # Add confirmation buttons
            buttons = [
                InteractiveButton(title="✅ Confirm Reservation", value="confirm"),
                InteractiveButton(title="❌ Cancel", value="cancel"),
                InteractiveButton(title="✏️ Make Changes", value="change")
            ]
            print("Confirmation request detected, adding buttons", flush=True)
        
        # Check if this is a completed reservation (user confirmed)
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
                        
                        # Save to database WITH table assignment
                        self._save_reservation(
                            reservation_data, 
                            sender_phone or '',
                            table_availability=table_availability
                        )
                        
                        # Build table info for confirmation message
                        table_info = ""
                        if table_availability and table_availability.available:
                            table_desc = table_availability.table_id
                            if table_availability.table_name:
                                table_desc = f"{table_availability.table_id} ({table_availability.table_name})"
                            table_info = f"\n🪑 Table: {table_desc} ({table_availability.seats} seats)"
                        
                        # Generate confirmation message
                        special_req_text = f"\n📝 Special Requests: {reservation_data['special_requests']}" if reservation_data.get('special_requests') else ""
                        
                        final_response = f"""✅ Reservation Confirmed!

📅 Date: {reservation_data['date']}
🕐 Time: {reservation_data['time']}
👥 Guests: {reservation_data['guests']}
👤 Name: {reservation_data['name']}
📞 Phone: {reservation_data['phone']}{table_info}{special_req_text}

Thank you for your reservation! We look forward to welcoming you. You will receive a confirmation shortly."""
                        
                        # Clear conversation history after successful reservation
                        clear_conversation_history(self.restaurant_id, conversation_id, self.app)
                        conversation_cleared = True
                        buttons = None  # No buttons needed after confirmation
                        needs_confirmation = False
                        
                        print(f"Reservation completed and saved!", flush=True)
                        
            except (json.JSONDecodeError, ValidationError, KeyError) as e:
                print(f"Failed to parse reservation JSON: {e}", flush=True)
        
        return AssistantResponse(
            text=final_response,
            reservation=reservation_data,
            conversation_cleared=conversation_cleared,
            buttons=buttons,
            needs_confirmation=needs_confirmation
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
        
        # Convert buttons to dict format
        buttons_data = None
        if response.buttons:
            buttons_data = [{"title": b.title, "value": b.value} for b in response.buttons]
        
        # Return in format expected by webhook
        return json.dumps({
            "text": response.text,
            "buttons": buttons_data,
            "needs_confirmation": response.needs_confirmation,
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
