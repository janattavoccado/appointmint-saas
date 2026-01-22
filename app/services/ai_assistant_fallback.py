"""
Fallback AI Reservation Assistant Service using standard OpenAI API
This is used when the OpenAI Agents SDK is not available or has compatibility issues.
Features button-based conversation flow for guided booking experience.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from app.models import db, Restaurant, Table, Reservation, AIConversation
from app.services.datetime_utils import (
    get_current_datetime, 
    parse_relative_date, 
    parse_time, 
    parse_datetime,
    get_timezone,
    format_time_12h
)


class ReservationAssistantFallback:
    """AI-powered reservation assistant using standard OpenAI API with button support"""
    
    def __init__(self, restaurant_id: int, app=None):
        self.restaurant_id = restaurant_id
        self.app = app
        self._restaurant_info = None
        self._client = None
        self._timezone = 'UTC'
        
    def _get_client(self):
        """Get OpenAI client"""
        if self._client is None:
            from openai import OpenAI
            api_key = os.environ.get('OPENAI_API_KEY')
            if self.app:
                api_key = self.app.config.get('OPENAI_API_KEY') or api_key
            self._client = OpenAI(api_key=api_key)
        return self._client
        
    def _get_restaurant_info(self) -> Dict[str, Any]:
        """Get restaurant information from database"""
        if self._restaurant_info:
            return self._restaurant_info
            
        with self.app.app_context():
            restaurant = Restaurant.query.get(self.restaurant_id)
            if not restaurant:
                return {}
            
            # Store timezone for date/time operations
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
    
    def _get_next_5_dates(self) -> List[Dict[str, str]]:
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
            
            dates.append({
                'value': date.strftime('%Y-%m-%d'),
                'display': display
            })
        
        return dates
    
    def _get_guest_buttons(self) -> List[Dict[str, Any]]:
        """Get guest count buttons 1-8"""
        buttons = []
        for i in range(1, 9):
            buttons.append({
                'value': str(i),
                'display': f"{i} {'guest' if i == 1 else 'guests'}"
            })
        buttons.append({
            'value': 'more',
            'display': '9+ guests (special request)'
        })
        return buttons
    
    def _format_response_with_buttons(self, message: str, buttons: List[Dict], button_type: str) -> Dict[str, Any]:
        """Format a response with interactive buttons"""
        return {
            'message': message,
            'buttons': buttons,
            'button_type': button_type  # 'date', 'guests', 'confirm', 'time'
        }
    
    def _get_tools(self) -> List[Dict]:
        """Define tools for the AI assistant"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_current_datetime",
                    "description": "Get the current date and time in the restaurant's timezone. Call this at the start of every conversation.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "show_date_selection",
                    "description": "Show date selection buttons to the customer. Call this when starting a new reservation to let the customer pick a date.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "show_guest_selection",
                    "description": "Show guest count selection buttons (1-8, plus 9+ for special requests). Call this after the customer has selected a date and time.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "show_confirmation",
                    "description": "Show a confirmation button with all reservation details. Call this after collecting all information (date, time, guests, name, phone).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "Reservation date in YYYY-MM-DD format"},
                            "time": {"type": "string", "description": "Reservation time in HH:MM format"},
                            "party_size": {"type": "integer", "description": "Number of guests"},
                            "customer_name": {"type": "string", "description": "Customer's name"},
                            "customer_phone": {"type": "string", "description": "Customer's phone number"}
                        },
                        "required": ["date", "time", "party_size", "customer_name", "customer_phone"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "parse_date_time",
                    "description": "Parse natural language date and time expressions into standardized formats.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {
                                "type": "string",
                                "description": "The date/time expression to parse"
                            }
                        },
                        "required": ["expression"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "check_availability",
                    "description": "Check table availability for a specific date, time, and party size.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                            "time": {"type": "string", "description": "Time in HH:MM format (24-hour)"},
                            "party_size": {"type": "integer", "description": "Number of guests"}
                        },
                        "required": ["date", "time", "party_size"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "make_reservation",
                    "description": "Create a new table reservation after customer confirms.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "customer_name": {"type": "string", "description": "Full name of the customer"},
                            "customer_phone": {"type": "string", "description": "Contact phone number"},
                            "party_size": {"type": "integer", "description": "Number of guests"},
                            "date": {"type": "string", "description": "Reservation date in YYYY-MM-DD format"},
                            "time": {"type": "string", "description": "Reservation time in HH:MM format (24-hour)"},
                            "customer_email": {"type": "string", "description": "Optional email address"},
                            "special_requests": {"type": "string", "description": "Optional special requests"}
                        },
                        "required": ["customer_name", "customer_phone", "party_size", "date", "time"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_restaurant_info",
                    "description": "Get information about the restaurant",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "request_large_party_booking",
                    "description": "Handle booking requests for 9+ guests. These require staff follow-up.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "party_size": {"type": "integer", "description": "Number of guests"},
                            "customer_name": {"type": "string", "description": "Customer's name"},
                            "customer_phone": {"type": "string", "description": "Customer's phone number"},
                            "preferred_date": {"type": "string", "description": "Preferred date"},
                            "preferred_time": {"type": "string", "description": "Preferred time"}
                        },
                        "required": ["party_size", "customer_name", "customer_phone"]
                    }
                }
            }
        ]
    
    def _execute_tool(self, tool_name: str, arguments: Dict) -> str:
        """Execute a tool and return the result"""
        try:
            if tool_name == "get_current_datetime":
                return self._get_current_datetime_tool()
            elif tool_name == "show_date_selection":
                return self._show_date_selection_tool()
            elif tool_name == "show_guest_selection":
                return self._show_guest_selection_tool()
            elif tool_name == "show_confirmation":
                return self._show_confirmation_tool(arguments)
            elif tool_name == "parse_date_time":
                return self._parse_date_time_tool(arguments.get('expression', ''))
            elif tool_name == "check_availability":
                return self._check_availability(
                    arguments.get('date'),
                    arguments.get('time'),
                    arguments.get('party_size')
                )
            elif tool_name == "make_reservation":
                return self._make_reservation(
                    arguments.get('customer_name'),
                    arguments.get('customer_phone'),
                    arguments.get('party_size'),
                    arguments.get('date'),
                    arguments.get('time'),
                    arguments.get('customer_email', ''),
                    arguments.get('special_requests', '')
                )
            elif tool_name == "get_restaurant_info":
                return self._get_restaurant_info_tool()
            elif tool_name == "request_large_party_booking":
                return self._request_large_party_booking(arguments)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"
    
    def _get_current_datetime_tool(self) -> str:
        """Get current datetime in restaurant's timezone"""
        self._get_restaurant_info()  # Ensure timezone is loaded
        datetime_info = get_current_datetime(self._timezone)
        return json.dumps({
            'current_date': datetime_info['current_date'],
            'current_time': datetime_info['current_time'],
            'day_of_week': datetime_info['day_of_week'],
            'timezone': self._timezone,
            'formatted': f"{datetime_info['day_of_week']}, {datetime_info['current_date']} at {datetime_info['current_time']}"
        })
    
    def _show_date_selection_tool(self) -> str:
        """Return date selection buttons"""
        self._get_restaurant_info()  # Ensure timezone is loaded
        dates = self._get_next_5_dates()
        return json.dumps({
            'action': 'show_buttons',
            'button_type': 'date',
            'buttons': dates,
            'message': 'Please select a date for your reservation:'
        })
    
    def _show_guest_selection_tool(self) -> str:
        """Return guest count selection buttons"""
        buttons = self._get_guest_buttons()
        return json.dumps({
            'action': 'show_buttons',
            'button_type': 'guests',
            'buttons': buttons,
            'message': 'How many guests will be dining?'
        })
    
    def _show_confirmation_tool(self, args: Dict) -> str:
        """Return confirmation button with booking details"""
        date_str = args.get('date', '')
        time_str = args.get('time', '')
        party_size = args.get('party_size', 0)
        customer_name = args.get('customer_name', '')
        customer_phone = args.get('customer_phone', '')
        
        # Format date for display
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            date_display = date_obj.strftime('%A, %B %d, %Y')
        except:
            date_display = date_str
        
        # Format time for display
        try:
            hour, minute = map(int, time_str.split(':'))
            time_display = format_time_12h(hour, minute)
        except:
            time_display = time_str
        
        return json.dumps({
            'action': 'show_buttons',
            'button_type': 'confirm',
            'buttons': [
                {'value': 'confirm', 'display': '✓ Confirm Booking'},
                {'value': 'cancel', 'display': '✗ Cancel'}
            ],
            'booking_details': {
                'date': date_str,
                'date_display': date_display,
                'time': time_str,
                'time_display': time_display,
                'party_size': party_size,
                'customer_name': customer_name,
                'customer_phone': customer_phone
            },
            'message': f"Please confirm your reservation:\n\n📅 Date: {date_display}\n⏰ Time: {time_display}\n👥 Guests: {party_size}\n👤 Name: {customer_name}\n📱 Phone: {customer_phone}"
        })
    
    def _parse_date_time_tool(self, expression: str) -> str:
        """Parse natural language date/time expression"""
        self._get_restaurant_info()  # Ensure timezone is loaded
        result = parse_datetime(expression, self._timezone)
        return json.dumps(result)
    
    def _check_availability(self, date_str: str, time_str: str, party_size: int) -> str:
        """Check table availability"""
        with self.app.app_context():
            try:
                check_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                check_time = datetime.strptime(time_str, '%H:%M').time()
            except ValueError as e:
                return f"Invalid date or time format: {e}"
            
            # Find suitable tables
            suitable_tables = Table.query.filter(
                Table.restaurant_id == self.restaurant_id,
                Table.capacity >= party_size,
                Table.is_active == True
            ).order_by(Table.capacity).all()
            
            if not suitable_tables:
                return f"Sorry, we don't have tables that can accommodate {party_size} guests."
            
            # Check for existing reservations
            available_tables = []
            for table in suitable_tables:
                existing = Reservation.query.filter(
                    Reservation.table_id == table.id,
                    Reservation.reservation_date == check_date,
                    Reservation.reservation_time == check_time,
                    Reservation.status.in_(['pending', 'confirmed'])
                ).first()
                
                if not existing:
                    available_tables.append({
                        'id': table.id,
                        'name': table.name,
                        'capacity': table.capacity,
                        'location': table.location or 'Main Floor'
                    })
            
            # Format date and time for display
            date_display = check_date.strftime('%A, %B %d, %Y')
            time_display = format_time_12h(check_time.hour, check_time.minute)
            
            if available_tables:
                return json.dumps({
                    'available': True,
                    'date': date_str,
                    'date_display': date_display,
                    'time': time_str,
                    'time_display': time_display,
                    'party_size': party_size,
                    'tables': available_tables,
                    'message': f"Great news! We have availability on {date_display} at {time_display} for {party_size} guests."
                })
            else:
                return json.dumps({
                    'available': False,
                    'date': date_str,
                    'date_display': date_display,
                    'time': time_str,
                    'time_display': time_display,
                    'party_size': party_size,
                    'message': f"Sorry, we're fully booked on {date_display} at {time_display}. Would you like to try a different time?"
                })
    
    def _make_reservation(self, customer_name: str, customer_phone: str, 
                          party_size: int, date_str: str, time_str: str,
                          customer_email: str = '', special_requests: str = '') -> str:
        """Create a reservation"""
        with self.app.app_context():
            try:
                res_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                res_time = datetime.strptime(time_str, '%H:%M').time()
            except ValueError as e:
                return f"Invalid date or time format: {e}"
            
            # Find an available table
            suitable_tables = Table.query.filter(
                Table.restaurant_id == self.restaurant_id,
                Table.capacity >= party_size,
                Table.is_active == True
            ).order_by(Table.capacity).all()
            
            selected_table = None
            for table in suitable_tables:
                existing = Reservation.query.filter(
                    Reservation.table_id == table.id,
                    Reservation.reservation_date == res_date,
                    Reservation.reservation_time == res_time,
                    Reservation.status.in_(['pending', 'confirmed'])
                ).first()
                
                if not existing:
                    selected_table = table
                    break
            
            if not selected_table:
                return "Sorry, no tables are available for this time slot."
            
            # Create reservation
            reservation = Reservation(
                restaurant_id=self.restaurant_id,
                table_id=selected_table.id,
                customer_name=customer_name,
                customer_email=customer_email,
                customer_phone=customer_phone,
                party_size=party_size,
                reservation_date=res_date,
                reservation_time=res_time,
                special_requests=special_requests,
                source='ai_assistant',
                status='confirmed'
            )
            db.session.add(reservation)
            
            # Update trial booking count if applicable
            restaurant = Restaurant.query.get(self.restaurant_id)
            if restaurant and restaurant.tenant:
                tenant = restaurant.tenant
                if hasattr(tenant, 'trial_booking_count') and tenant.trial_booking_count is not None:
                    tenant.trial_booking_count += 1
            
            db.session.commit()
            
            # Format for display
            date_display = res_date.strftime('%A, %B %d, %Y')
            time_display = format_time_12h(res_time.hour, res_time.minute)
            restaurant_name = self._get_restaurant_info().get('name', 'our restaurant')
            
            return json.dumps({
                'success': True,
                'confirmation_number': reservation.id,
                'date_display': date_display,
                'time_display': time_display,
                'party_size': party_size,
                'customer_name': customer_name,
                'table_name': selected_table.name,
                'table_location': selected_table.location or 'Main Floor',
                'message': f"Your reservation is confirmed! Confirmation #{reservation.id}. We look forward to seeing you at {restaurant_name}!"
            })
    
    def _get_restaurant_info_tool(self) -> str:
        """Get restaurant information"""
        info = self._get_restaurant_info()
        return json.dumps({
            'name': info.get('name'),
            'address': info.get('address'),
            'city': info.get('city'),
            'phone': info.get('phone'),
            'cuisine_type': info.get('cuisine_type'),
            'description': info.get('description')
        })
    
    def _request_large_party_booking(self, args: Dict) -> str:
        """Handle large party booking requests (9+ guests)"""
        with self.app.app_context():
            # Create a special request reservation with pending status
            party_size = args.get('party_size', 9)
            customer_name = args.get('customer_name', '')
            customer_phone = args.get('customer_phone', '')
            preferred_date = args.get('preferred_date', '')
            preferred_time = args.get('preferred_time', '')
            
            # Parse date if provided
            res_date = None
            res_time = None
            if preferred_date:
                try:
                    res_date = datetime.strptime(preferred_date, '%Y-%m-%d').date()
                except:
                    pass
            if preferred_time:
                try:
                    res_time = datetime.strptime(preferred_time, '%H:%M').time()
                except:
                    pass
            
            # Create a pending reservation for staff follow-up
            reservation = Reservation(
                restaurant_id=self.restaurant_id,
                customer_name=customer_name,
                customer_phone=customer_phone,
                party_size=party_size,
                reservation_date=res_date,
                reservation_time=res_time,
                special_requests=f"Large party booking request for {party_size} guests. Staff follow-up required.",
                source='ai_assistant',
                status='pending'
            )
            db.session.add(reservation)
            db.session.commit()
            
            return json.dumps({
                'success': True,
                'request_id': reservation.id,
                'message': f"Thank you, {customer_name}! Your request for a party of {party_size} has been submitted. Our staff will contact you at {customer_phone} within 24 hours to confirm availability and finalize your booking."
            })
    
    def _get_system_prompt(self) -> str:
        """Get the system prompt for the AI assistant"""
        restaurant_info = self._get_restaurant_info()
        restaurant_name = restaurant_info.get('name', 'our restaurant')
        timezone = restaurant_info.get('timezone', 'UTC')
        knowledge_base = restaurant_info.get('knowledge_base', '')
        
        # Build knowledge base section if available
        kb_section = ''
        if knowledge_base:
            kb_section = f"""

KNOWLEDGE BASE - Use this information to answer customer questions:
--- START KNOWLEDGE BASE ---
{knowledge_base}
--- END KNOWLEDGE BASE ---

When customers ask questions about the menu, hours, location, policies, or other information:
- Answer based on the knowledge base above
- If the information is not in the knowledge base, politely say you don't have that information
- After answering, offer to help with a reservation
"""
        
        return f"""You are a friendly and professional AI reservation assistant for {restaurant_name}.{kb_section}

Your role is to LEAD the conversation and guide customers through the booking process step by step.

IMPORTANT - YOU MUST LEAD THE CONVERSATION:
1. When a customer wants to make a reservation, YOU take charge and guide them
2. Use the interactive button tools to make it easy for customers to select options
3. Collect information in this EXACT order:
   a) DATE - Call show_date_selection to show date buttons
   b) TIME - Ask the customer what time they prefer (let them type it)
   c) GUESTS - Call show_guest_selection to show guest count buttons (1-8)
   d) NAME - Ask for the customer's name
   e) PHONE - Ask for their phone number
   f) CONFIRM - Call show_confirmation to show the confirm button

CONVERSATION FLOW:
1. Greet the customer warmly
2. When they want to book, immediately call show_date_selection
3. After they select a date, ask "What time would you like to dine?"
4. After they provide time, call show_guest_selection
5. If they select 9+ guests, use request_large_party_booking
6. After they select guests (1-8), ask "May I have your name for the reservation?"
7. After they provide name, ask "And what's the best phone number to reach you?"
8. After they provide phone, call show_confirmation with all details
9. When they click Confirm, call make_reservation

BUTTON RESPONSES:
- When you call show_date_selection, show_guest_selection, or show_confirmation, the system will display interactive buttons
- The customer will click a button and their selection will come back as a message
- Process their selection and continue to the next step

IMPORTANT GUIDELINES:
- Be warm, friendly, and conversational
- Keep responses concise
- Always confirm details before finalizing
- The restaurant operates in the {timezone} timezone
- Use 12-hour format for times when displaying to customers
- If a time is not available, suggest alternatives

HANDLING SPECIAL CASES:
- For 9+ guests: Use request_large_party_booking - staff will follow up
- If customer provides all info at once, still confirm with show_confirmation
- If customer wants to change something, be flexible and helpful

VOICE INTERACTION:
- Keep responses concise for voice interactions
- Speak naturally as if having a phone conversation
"""
    
    def chat_sync(self, message: str, session_id: Optional[str] = None, 
                  conversation_history: Optional[List] = None,
                  is_session_start: bool = False) -> str:
        """
        Process a text message and return a response using OpenAI Chat Completions API.
        
        Args:
            message: The user's message
            session_id: Optional session ID for conversation continuity
            conversation_history: Optional list of previous messages
            is_session_start: If True, automatically call get_current_datetime first
        
        Returns:
            The assistant's response (may include JSON for button rendering)
        """
        try:
            client = self._get_client()
            
            # Build messages
            messages = [{"role": "system", "content": self._get_system_prompt()}]
            
            # If this is session start, inject datetime context
            if is_session_start or not conversation_history:
                datetime_result = self._get_current_datetime_tool()
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "init_datetime",
                        "type": "function",
                        "function": {
                            "name": "get_current_datetime",
                            "arguments": "{}"
                        }
                    }]
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": "init_datetime",
                    "content": datetime_result
                })
            
            if conversation_history:
                messages.extend(conversation_history)
            
            messages.append({"role": "user", "content": message})
            
            # Make API call with tools
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=messages,
                tools=self._get_tools(),
                tool_choice="auto"
            )
            
            assistant_message = response.choices[0].message
            
            # Track button responses to include in final response
            button_data = None
            
            # Handle tool calls
            while assistant_message.tool_calls:
                # Add assistant message with tool calls
                messages.append({
                    "role": "assistant",
                    "content": assistant_message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in assistant_message.tool_calls
                    ]
                })
                
                # Execute each tool call
                for tool_call in assistant_message.tool_calls:
                    function_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)
                    
                    # Execute the tool
                    result = self._execute_tool(function_name, arguments)
                    
                    # Check if this is a button response
                    try:
                        result_data = json.loads(result)
                        if result_data.get('action') == 'show_buttons':
                            button_data = result_data
                    except:
                        pass
                    
                    # Add tool result
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result
                    })
                
                # Get next response
                response = client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=messages,
                    tools=self._get_tools(),
                    tool_choice="auto"
                )
                
                assistant_message = response.choices[0].message
            
            # Build final response
            text_response = assistant_message.content or "I'm sorry, I couldn't generate a response. Please try again."
            
            # If we have button data, include it in the response
            if button_data:
                return json.dumps({
                    'text': text_response,
                    'buttons': button_data.get('buttons', []),
                    'button_type': button_data.get('button_type', ''),
                    'booking_details': button_data.get('booking_details')
                })
            
            return text_response
            
        except Exception as e:
            return f"I apologize, but I encountered an error: {str(e)}. Please try again."
