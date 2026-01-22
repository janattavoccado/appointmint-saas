"""
API Routes for AppointMint saas
Includes AI-powered reservation assistant using OpenAI Agents SDK
"""

from flask import Blueprint, request, jsonify, current_app, Response
from app.models import db, Restaurant, Table, Reservation, AIConversation
from datetime import datetime, date
import os
import json
import base64
import asyncio

api_bp = Blueprint('api', __name__)


def get_openai_client():
    """Get OpenAI client"""
    try:
        from openai import OpenAI
        api_key = current_app.config.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY')
        if api_key:
            return OpenAI(api_key=api_key)
    except ImportError:
        pass
    return None


@api_bp.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})


@api_bp.route('/restaurants/<int:restaurant_id>/availability', methods=['GET'])
def check_availability(restaurant_id):
    """Check table availability for a restaurant"""
    restaurant = Restaurant.query.get_or_404(restaurant_id)

    date_str = request.args.get('date')
    time_str = request.args.get('time')
    party_size = request.args.get('party_size', type=int)

    if not all([date_str, time_str, party_size]):
        return jsonify({'error': 'Missing required parameters: date, time, party_size'}), 400

    try:
        check_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        check_time = datetime.strptime(time_str, '%H:%M').time()
    except ValueError:
        return jsonify({'error': 'Invalid date or time format'}), 400

    # Find available tables
    available_tables = Table.query.filter(
        Table.restaurant_id == restaurant_id,
        Table.capacity >= party_size,
        Table.is_active == True
    ).all()

    # Check for existing reservations
    available = []
    for table in available_tables:
        existing = Reservation.query.filter(
            Reservation.table_id == table.id,
            Reservation.reservation_date == check_date,
            Reservation.status.in_(['pending', 'confirmed'])
        ).all()

        # Simple availability check
        is_available = True
        for res in existing:
            if res.reservation_time == check_time:
                is_available = False
                break

        if is_available:
            available.append({
                'table_id': table.id,
                'table_name': table.name,
                'capacity': table.capacity,
                'location': table.location
            })

    return jsonify({
        'restaurant_id': restaurant_id,
        'date': date_str,
        'time': time_str,
        'party_size': party_size,
        'available_tables': available
    })


@api_bp.route('/reservations', methods=['POST'])
def create_reservation():
    """Create a new reservation"""
    data = request.get_json()

    required_fields = ['restaurant_id', 'customer_name', 'customer_phone',
                       'party_size', 'reservation_date', 'reservation_time']

    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    try:
        reservation = Reservation(
            restaurant_id=data['restaurant_id'],
            table_id=data.get('table_id'),
            customer_name=data['customer_name'],
            customer_email=data.get('customer_email'),
            customer_phone=data['customer_phone'],
            party_size=data['party_size'],
            reservation_date=datetime.strptime(data['reservation_date'], '%Y-%m-%d').date(),
            reservation_time=datetime.strptime(data['reservation_time'], '%H:%M').time(),
            duration_minutes=data.get('duration_minutes', 90),
            special_requests=data.get('special_requests'),
            source=data.get('source', 'api')
        )
        db.session.add(reservation)
        db.session.commit()

        return jsonify({
            'success': True,
            'reservation_id': reservation.id,
            'message': 'Reservation created successfully'
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@api_bp.route('/reservations/<int:id>', methods=['GET'])
def get_reservation(id):
    """Get reservation details"""
    reservation = Reservation.query.get_or_404(id)

    return jsonify({
        'id': reservation.id,
        'restaurant_id': reservation.restaurant_id,
        'restaurant_name': reservation.restaurant.name,
        'customer_name': reservation.customer_name,
        'customer_phone': reservation.customer_phone,
        'party_size': reservation.party_size,
        'reservation_date': reservation.reservation_date.isoformat(),
        'reservation_time': reservation.reservation_time.strftime('%H:%M'),
        'status': reservation.status,
        'special_requests': reservation.special_requests
    })


@api_bp.route('/reservations/<int:id>', methods=['PUT'])
def update_reservation(id):
    """Update a reservation"""
    reservation = Reservation.query.get_or_404(id)
    data = request.get_json()

    if 'status' in data:
        reservation.status = data['status']
    if 'table_id' in data:
        reservation.table_id = data['table_id']
    if 'special_requests' in data:
        reservation.special_requests = data['special_requests']

    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Reservation updated successfully'
    })


@api_bp.route('/reservations/<int:id>', methods=['DELETE'])
def cancel_reservation(id):
    """Cancel a reservation"""
    reservation = Reservation.query.get_or_404(id)
    reservation.status = 'cancelled'
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Reservation cancelled successfully'
    })


# =============================================================================
# AI ASSISTANT ENDPOINTS (Using OpenAI Agents SDK)
# =============================================================================

@api_bp.route('/ai/chat', methods=['POST'])
def ai_chat():
    """
    AI chat endpoint for reservation assistance using OpenAI Agents SDK.

    Request body:
    {
        "message": "I want to make a reservation",
        "restaurant_id": 1,
        "session_id": "optional-session-id",
        "conversation_history": [{"role": "user", "content": "..."}]
    }
    """
    data = request.get_json()
    message = data.get('message')
    restaurant_id = data.get('restaurant_id')
    session_id = data.get('session_id')
    conversation_history = data.get('conversation_history', [])

    if not message:
        return jsonify({'error': 'Message is required'}), 400

    if not restaurant_id:
        return jsonify({'error': 'Restaurant ID is required'}), 400

    # Verify restaurant exists
    restaurant = Restaurant.query.get(restaurant_id)
    if not restaurant:
        return jsonify({'error': 'Restaurant not found'}), 404

    # Determine if this is a new session (no conversation history)
    is_session_start = not conversation_history or len(conversation_history) == 0

    try:
        # Try using OpenAI Agents SDK first (Python 3.13+)
        try:
            from app.services.ai_assistant import get_assistant
            assistant = get_assistant(restaurant_id, current_app._get_current_object())
            response = assistant.chat_sync(message, session_id, conversation_history)
        except Exception as agents_error:
            # Fall back to standard OpenAI API
            current_app.logger.warning(f"Agents SDK failed, using fallback: {agents_error}")
            from app.services.ai_assistant_fallback import ReservationAssistantFallback
            assistant = ReservationAssistantFallback(restaurant_id, current_app._get_current_object())
            response = assistant.chat_sync(message, session_id, conversation_history)

        # Log conversation
        conversation = AIConversation(
            restaurant_id=restaurant_id,
            conversation_type='text',
            transcript=f"User: {message}\nAI: {response}",
            tokens_used=0  # Token tracking can be added later
        )
        db.session.add(conversation)
        db.session.commit()

        return jsonify({
            'success': True,
            'response': response,
            'restaurant_id': restaurant_id,
            'session_id': session_id
        })

    except Exception as e:
        current_app.logger.error(f"AI Chat Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/ai/transcribe', methods=['POST'])
def ai_transcribe():
    """
    Transcribe audio to text using OpenAI Whisper.

    Request: multipart/form-data with 'audio' file
    or JSON with 'audio_base64' field
    """
    client = get_openai_client()
    if not client:
        return jsonify({'error': 'OpenAI API not configured'}), 500

    try:
        audio_data = None

        # Check for file upload
        if 'audio' in request.files:
            audio_file = request.files['audio']
            audio_data = audio_file.read()
            filename = audio_file.filename or 'audio.webm'
        # Check for base64 encoded audio
        elif request.is_json:
            data = request.get_json()
            if 'audio_base64' in data:
                audio_data = base64.b64decode(data['audio_base64'])
                filename = data.get('filename', 'audio.webm')

        if not audio_data:
            return jsonify({'error': 'No audio data provided'}), 400

        # Save temporarily and transcribe
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = tmp.name

        try:
            with open(tmp_path, 'rb') as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file
                )

            return jsonify({
                'text': transcript.text,
                'success': True
            })
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        current_app.logger.error(f"Transcription Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/ai/speak', methods=['POST'])
def ai_speak():
    """
    Convert text to speech using OpenAI TTS.

    Request body:
    {
        "text": "Hello, how can I help you?",
        "voice": "alloy"  // optional: alloy, echo, fable, onyx, nova, shimmer
    }
    """
    client = get_openai_client()
    if not client:
        return jsonify({'error': 'OpenAI API not configured'}), 500

    data = request.get_json()
    text = data.get('text')
    voice = data.get('voice', 'alloy')

    if not text:
        return jsonify({'error': 'Text is required'}), 400

    try:
        response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text
        )

        # Return audio as base64
        audio_data = response.content
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')

        return jsonify({
            'audio_base64': audio_base64,
            'content_type': 'audio/mpeg',
            'success': True
        })

    except Exception as e:
        current_app.logger.error(f"TTS Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/ai/voice-chat', methods=['POST'])
def ai_voice_chat():
    """
    Voice chat: transcribe user audio input, get AI text response.
    User can speak, but AI always responds with text only (no audio).

    Request: multipart/form-data with 'audio' file and 'restaurant_id'
    or JSON with 'audio_base64', 'restaurant_id'
    """
    client = get_openai_client()
    if not client:
        return jsonify({'error': 'OpenAI API not configured'}), 500

    try:
        # Get restaurant_id
        restaurant_id = request.form.get('restaurant_id') or (request.get_json() or {}).get('restaurant_id')
        if not restaurant_id:
            return jsonify({'error': 'Restaurant ID is required'}), 400

        restaurant_id = int(restaurant_id)
        restaurant = Restaurant.query.get(restaurant_id)
        if not restaurant:
            return jsonify({'error': 'Restaurant not found'}), 404

        # Get audio data
        audio_data = None
        if 'audio' in request.files:
            audio_file = request.files['audio']
            audio_data = audio_file.read()
        elif request.is_json:
            data = request.get_json()
            if 'audio_base64' in data:
                audio_data = base64.b64decode(data['audio_base64'])

        if not audio_data:
            return jsonify({'error': 'No audio data provided'}), 400

        # Step 1: Transcribe audio
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = tmp.name

        try:
            with open(tmp_path, 'rb') as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file
                )
            user_text = transcript.text
        finally:
            os.unlink(tmp_path)

        # Get conversation history from request if available
        conversation_history = []
        if request.is_json:
            conversation_history = request.get_json().get('conversation_history', [])
        elif request.form.get('conversation_history'):
            try:
                conversation_history = json.loads(request.form.get('conversation_history', '[]'))
            except:
                conversation_history = []

        is_session_start = not conversation_history or len(conversation_history) == 0

        # Step 2: Get AI response (text only, no audio)
        try:
            from app.services.ai_assistant import get_assistant
            assistant = get_assistant(restaurant_id, current_app._get_current_object())
            ai_response = assistant.chat_sync(user_text, None, conversation_history, is_session_start)
        except Exception as agents_error:
            # Fall back to standard OpenAI API
            current_app.logger.warning(f"Agents SDK failed, using fallback: {agents_error}")
            from app.services.ai_assistant_fallback import ReservationAssistantFallback
            assistant = ReservationAssistantFallback(restaurant_id, current_app._get_current_object())
            ai_response = assistant.chat_sync(user_text, None, conversation_history, is_session_start)

        # Log conversation
        conversation = AIConversation(
            restaurant_id=restaurant_id,
            conversation_type='voice',
            transcript=f"User: {user_text}\nAI: {ai_response}",
            tokens_used=0
        )
        db.session.add(conversation)
        db.session.commit()

        # Return text response only (no audio)
        return jsonify({
            'user_text': user_text,
            'ai_response': ai_response,
            'success': True
        })

    except Exception as e:
        current_app.logger.error(f"Voice Chat Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# WIDGET ENDPOINTS
# =============================================================================

@api_bp.route('/widget/<int:restaurant_id>/config', methods=['GET'])
def get_widget_config(restaurant_id):
    """
    Get widget configuration for a restaurant.
    This endpoint is called by the embeddable widget.
    """
    restaurant = Restaurant.query.get_or_404(restaurant_id)

    return jsonify({
        'restaurant_id': restaurant.id,
        'restaurant_name': restaurant.name,
        'cuisine_type': restaurant.cuisine_type,
        'welcome_message': f"Welcome to {restaurant.name}! I'm your AI reservation assistant. How can I help you today?",
        'theme': {
            'primary_color': '#2DD4BF',
            'secondary_color': '#1e293b',
            'font_family': 'Inter, sans-serif'
        },
        'features': {
            'voice_enabled': True,
            'text_enabled': True
        }
    })


@api_bp.route('/widget/<int:restaurant_id>/embed-code', methods=['GET'])
def get_widget_embed_code(restaurant_id):
    """
    Get the embeddable widget code for a restaurant.
    """
    restaurant = Restaurant.query.get_or_404(restaurant_id)

    # Get the base URL
    base_url = request.host_url.rstrip('/')

    embed_code = f'''<!-- AppointMint Reservation Widget for {restaurant.name} -->
<div id="appointmint-widget" data-restaurant-id="{restaurant_id}"></div>
<script src="{base_url}/static/js/widget.js"></script>
<link rel="stylesheet" href="{base_url}/static/css/widget.css">
<script>
  AppointMintWidget.init({{
    restaurantId: {restaurant_id},
    apiUrl: '{base_url}/api',
    theme: {{
      primaryColor: '#2DD4BF',
      position: 'bottom-right'
    }}
  }});
</script>
'''

    return jsonify({
        'restaurant_id': restaurant_id,
        'restaurant_name': restaurant.name,
        'embed_code': embed_code,
        'instructions': '''
To add the AppointMint reservation widget to your website:

1. Copy the embed code above
2. Paste it just before the closing </body> tag on your website
3. The widget will appear as a chat button in the bottom-right corner

Customization options:
- theme.primaryColor: Change the widget color (default: #2DD4BF)
- theme.position: 'bottom-right' or 'bottom-left'
'''
    })



# =============================================================================
# STAFF ASSISTANT ENDPOINTS
# =============================================================================

@api_bp.route('/staff/chat', methods=['POST'])
def staff_chat():
    """
    Staff Assistant chat endpoint for managing reservations.

    Request body:
    {
        "message": "show today's reservations",
        "restaurant_id": 1,
        "conversation_history": [{"role": "user", "content": "..."}]
    }
    """
    data = request.get_json()
    message = data.get('message')
    restaurant_id = data.get('restaurant_id')
    conversation_history = data.get('conversation_history', [])

    if not message:
        return jsonify({'error': 'Message is required'}), 400

    if not restaurant_id:
        return jsonify({'error': 'Restaurant ID is required'}), 400

    # Verify restaurant exists
    restaurant = Restaurant.query.get(restaurant_id)
    if not restaurant:
        return jsonify({'error': 'Restaurant not found'}), 404

    try:
        from app.services.staff_assistant import StaffAssistant
        assistant = StaffAssistant(restaurant_id)
        response = assistant.chat_sync(message, conversation_history)

        return jsonify({
            'success': True,
            'response': response,
            'restaurant_id': restaurant_id
        })

    except Exception as e:
        current_app.logger.error(f"Staff Chat Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/staff/quick-command', methods=['POST'])
def staff_quick_command():
    """
    Execute a quick command for the staff assistant.

    Request body:
    {
        "command": "todays_reservations" | "upcoming" | "stats" | "pending" | "confirmed",
        "restaurant_id": 1,
        "params": {}  // Optional parameters
    }
    """
    data = request.get_json()
    command = data.get('command')
    restaurant_id = data.get('restaurant_id')
    params = data.get('params', {})

    if not command:
        return jsonify({'error': 'Command is required'}), 400

    if not restaurant_id:
        return jsonify({'error': 'Restaurant ID is required'}), 400

    # Verify restaurant exists
    restaurant = Restaurant.query.get(restaurant_id)
    if not restaurant:
        return jsonify({'error': 'Restaurant not found'}), 404

    try:
        from app.services.staff_assistant import StaffAssistant
        assistant = StaffAssistant(restaurant_id)

        result = None
        if command == 'todays_reservations':
            result = assistant.get_todays_reservations()
        elif command == 'upcoming':
            hours = params.get('hours', 2)
            result = assistant.get_upcoming_reservations(hours)
        elif command == 'stats':
            result = assistant.get_todays_stats()
        elif command == 'pending':
            result = assistant.get_pending_bookings()
        elif command == 'confirmed':
            result = assistant.get_confirmed_bookings()
        else:
            return jsonify({'error': f'Unknown command: {command}'}), 400

        return jsonify({
            'success': True,
            'data': result,
            'restaurant_id': restaurant_id
        })

    except Exception as e:
        current_app.logger.error(f"Staff Quick Command Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/staff/update-status', methods=['POST'])
def staff_update_status():
    """
    Quick status update for a reservation.

    Request body:
    {
        "reservation_id": 1,
        "status": "confirmed" | "arrived" | "seated" | "completed" | "cancelled" | "no_show",
        "restaurant_id": 1
    }
    """
    data = request.get_json()
    reservation_id = data.get('reservation_id')
    status = data.get('status')
    restaurant_id = data.get('restaurant_id')

    if not reservation_id:
        return jsonify({'error': 'Reservation ID is required'}), 400

    if not status:
        return jsonify({'error': 'Status is required'}), 400

    if not restaurant_id:
        return jsonify({'error': 'Restaurant ID is required'}), 400

    try:
        from app.services.staff_assistant import StaffAssistant
        assistant = StaffAssistant(restaurant_id)
        result = assistant.update_reservation_status(reservation_id, status)

        return jsonify({
            'success': result.get('type') == 'success',
            'data': result,
            'restaurant_id': restaurant_id
        })

    except Exception as e:
        current_app.logger.error(f"Staff Update Status Error: {str(e)}")
        return jsonify({'error': str(e)}), 500



# =============================================================================
# CHATWOOT WEBHOOK INTEGRATION
# =============================================================================

@api_bp.route('/webhook/chatwoot/<webhook_token>', methods=['POST'])
def chatwoot_webhook(webhook_token):
    """
    Chatwoot webhook endpoint for receiving messages and sending AI responses.

    Each restaurant has a unique webhook token for security.

    Chatwoot sends webhook events like:
    - message_created: When a new message is received
    - conversation_created: When a new conversation starts
    - conversation_status_changed: When conversation status changes

    Webhook URL format: https://your-domain.com/api/webhook/chatwoot/{webhook_token}
    """
    # Find restaurant by webhook token
    restaurant = Restaurant.query.filter_by(webhook_token=webhook_token).first()

    if not restaurant:
        current_app.logger.warning(f"Invalid webhook token: {webhook_token}")
        return jsonify({'error': 'Invalid webhook token'}), 401

    # Get webhook payload
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data received'}), 400

    event_type = data.get('event')

    current_app.logger.info(f"Chatwoot webhook received for restaurant {restaurant.id}: {event_type}")

    # Handle message_created event
    if event_type == 'message_created':
        return handle_chatwoot_message(restaurant, data)

    # Handle conversation_created event
    elif event_type == 'conversation_created':
        return handle_chatwoot_conversation_created(restaurant, data)

    # Acknowledge other events
    return jsonify({'status': 'received', 'event': event_type})


def handle_chatwoot_message(restaurant, data):
    """
    Handle incoming message from Chatwoot and send AI response.
    Supports both regular webhook and Agent Bot payload formats.
    """
    try:
        # Log the full payload for debugging
        current_app.logger.info(f"=== CHATWOOT WEBHOOK RECEIVED ===")
        current_app.logger.info(f"Restaurant ID: {restaurant.id}")
        current_app.logger.info(f"Full payload: {json.dumps(data, default=str)[:1000]}")

        message_data = data.get('message', {})
        conversation_data = data.get('conversation', {})

        # Get message type - can be 'incoming', 0, or 1
        # Chatwoot uses: 0 = incoming, 1 = outgoing, 2 = activity
        message_type = message_data.get('message_type')

        # Handle both string and integer message types
        is_incoming = message_type in ['incoming', 0, '0']

        if not is_incoming:
            current_app.logger.info(f"Ignoring non-incoming message type: {message_type}")
            return jsonify({'status': 'ignored', 'reason': f'Not an incoming message (type: {message_type})'})

        # Get message content
        content = message_data.get('content', '')
        if not content:
            current_app.logger.info("Empty message content received")
            return jsonify({'status': 'ignored', 'reason': 'Empty message'})

        # Get conversation ID for session tracking
        # Try multiple locations where conversation_id might be
        conversation_id = conversation_data.get('id') or data.get('conversation_id') or message_data.get('conversation_id')

        if not conversation_id:
            current_app.logger.error("No conversation_id found in payload")
            return jsonify({'status': 'error', 'reason': 'No conversation_id'}), 400

        # Get sender info
        sender = message_data.get('sender', {}) or data.get('sender', {})
        sender_name = sender.get('name', 'Customer')

        current_app.logger.info(f"Processing message from {sender_name}: {content[:50]}...")

        # Generate AI response
        try:
            # Try using OpenAI Agents SDK first
            try:
                from app.services.ai_assistant import get_assistant
                assistant = get_assistant(restaurant.id, current_app._get_current_object())
                ai_response = assistant.chat_sync(content, str(conversation_id), [])
            except Exception as agents_error:
                # Fall back to standard OpenAI API
                current_app.logger.warning(f"Agents SDK failed, using fallback: {agents_error}")
                from app.services.ai_assistant_fallback import ReservationAssistantFallback
                assistant = ReservationAssistantFallback(restaurant.id, current_app._get_current_object())
                ai_response = assistant.chat_sync(content, str(conversation_id), [])

            # Parse the response if it's JSON (contains buttons)
            try:
                response_data = json.loads(ai_response)
                text_response = response_data.get('text', ai_response)
            except (json.JSONDecodeError, TypeError):
                text_response = ai_response

            # Send response back to Chatwoot
            if restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
                send_chatwoot_response(restaurant, conversation_id, text_response)

            # Log conversation
            conversation = AIConversation(
                restaurant_id=restaurant.id,
                conversation_type='chatwoot',
                transcript=f"User ({sender_name}): {content}\nAI: {text_response}",
                tokens_used=0
            )
            db.session.add(conversation)
            db.session.commit()

            return jsonify({
                'status': 'success',
                'response_sent': True
            })

        except Exception as ai_error:
            current_app.logger.error(f"AI response error: {str(ai_error)}")
            return jsonify({
                'status': 'error',
                'error': str(ai_error)
            }), 500

    except Exception as e:
        current_app.logger.error(f"Chatwoot message handling error: {str(e)}")
        return jsonify({'error': str(e)}), 500


def handle_chatwoot_conversation_created(restaurant, data):
    """
    Handle new conversation created in Chatwoot.
    Optionally send a welcome message.
    """
    try:
        conversation_data = data.get('conversation', {})
        conversation_id = conversation_data.get('id')

        # Send welcome message if configured
        welcome_message = restaurant.widget_welcome_message or f"Hello! Welcome to {restaurant.name}. How can I help you with your reservation today?"

        if restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
            send_chatwoot_response(restaurant, conversation_id, welcome_message)

        return jsonify({
            'status': 'success',
            'welcome_sent': True
        })

    except Exception as e:
        current_app.logger.error(f"Chatwoot conversation created error: {str(e)}")
        return jsonify({'error': str(e)}), 500


def send_chatwoot_response(restaurant, conversation_id, message):
    """
    Send a message back to Chatwoot conversation.
    Uses the Chatwoot Messages API: POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages
    """
    import requests

    current_app.logger.info(f"=== SENDING TO CHATWOOT ===")
    current_app.logger.info(f"Conversation ID: {conversation_id}")
    current_app.logger.info(f"Message: {message[:100]}..." if len(message) > 100 else f"Message: {message}")
    current_app.logger.info(f"Base URL: {restaurant.chatwoot_base_url}")
    current_app.logger.info(f"Account ID: {restaurant.chatwoot_account_id}")
    current_app.logger.info(f"API Key set: {bool(restaurant.chatwoot_api_key)}")

    if not all([restaurant.chatwoot_api_key, restaurant.chatwoot_base_url, restaurant.chatwoot_account_id]):
        current_app.logger.error(f"Chatwoot NOT fully configured for restaurant {restaurant.id}")
        current_app.logger.error(f"Missing - API Key: {not restaurant.chatwoot_api_key}, Base URL: {not restaurant.chatwoot_base_url}, Account ID: {not restaurant.chatwoot_account_id}")
        return False

    try:
        # Chatwoot API endpoint for sending messages
        base_url = restaurant.chatwoot_base_url.rstrip('/')
        url = f"{base_url}/api/v1/accounts/{restaurant.chatwoot_account_id}/conversations/{conversation_id}/messages"

        current_app.logger.info(f"Chatwoot API URL: {url}")

        headers = {
            'Content-Type': 'application/json',
            'api_access_token': restaurant.chatwoot_api_key
        }

        payload = {
            'content': message,
            'message_type': 'outgoing',
            'private': False,
            'content_type': 'text'
        }

        current_app.logger.info(f"Sending payload: {json.dumps(payload)[:200]}")

        response = requests.post(url, json=payload, headers=headers, timeout=10)

        current_app.logger.info(f"Chatwoot response status: {response.status_code}")
        current_app.logger.info(f"Chatwoot response body: {response.text[:500]}")

        if response.status_code in [200, 201]:
            current_app.logger.info(f"SUCCESS: Message sent to Chatwoot conversation {conversation_id}")
            return True
        else:
            current_app.logger.error(f"FAILED: Chatwoot API error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        current_app.logger.error(f"EXCEPTION sending to Chatwoot: {str(e)}")
        return False


@api_bp.route('/webhook/chatwoot/<webhook_token>/test', methods=['GET'])
def test_chatwoot_webhook(webhook_token):
    """
    Test endpoint to verify webhook configuration.
    """
    restaurant = Restaurant.query.filter_by(webhook_token=webhook_token).first()

    if not restaurant:
        return jsonify({'error': 'Invalid webhook token'}), 401

    return jsonify({
        'status': 'ok',
        'restaurant_id': restaurant.id,
        'restaurant_name': restaurant.name,
        'chatwoot_configured': bool(restaurant.chatwoot_api_key and restaurant.chatwoot_base_url),
        'message': 'Webhook is properly configured!'
    })
