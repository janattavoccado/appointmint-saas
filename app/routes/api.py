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
    
    Supports both:
    1. Regular Chatwoot webhooks (with event wrapper)
    2. Agent Bot webhooks (direct payload without event wrapper)

    Webhook URL format: https://your-domain.com/api/webhook/chatwoot/{webhook_token}
    """
    print(f"=== WEBHOOK HIT === Token: {webhook_token}", flush=True)
    
    # Find restaurant by webhook token
    restaurant = Restaurant.query.filter_by(webhook_token=webhook_token).first()

    if not restaurant:
        print(f"ERROR: Invalid webhook token: {webhook_token}", flush=True)
        return jsonify({'error': 'Invalid webhook token'}), 401

    print(f"Restaurant found: {restaurant.id} - {restaurant.name}", flush=True)

    # Get webhook payload
    payload = request.get_json()
    print(f"Payload received: {json.dumps(payload, default=str)[:1000]}", flush=True)

    if not payload:
        print("ERROR: No data received", flush=True)
        return jsonify({'error': 'No data received'}), 400

    # Check if this is an Agent Bot payload (no event wrapper) or regular webhook
    event_type = payload.get('event')
    print(f"Event type: {event_type}", flush=True)
    
    # Agent Bot payload - direct message format (no event wrapper)
    # Agent Bot sends: {message_type, conversation: {id}, sender: {}, content, ...}
    if not event_type and payload.get('message_type') is not None:
        print("Detected Agent Bot payload format", flush=True)
        return handle_agent_bot_message(restaurant, payload)
    
    # Regular webhook with event wrapper
    if event_type == 'message_created':
        print("Handling message_created event...", flush=True)
        return handle_chatwoot_message(restaurant, payload)
    elif event_type == 'conversation_created':
        print("Handling conversation_created event...", flush=True)
        return handle_chatwoot_conversation_created(restaurant, payload)
    
    # Acknowledge other events or unknown format
    print(f"Unknown event/format, acknowledging...", flush=True)
    return jsonify({'status': 'received', 'event': event_type})


def handle_agent_bot_message(restaurant, payload):
    """
    Handle incoming message from Chatwoot Agent Bot.
    Agent Bot payload format is different from regular webhooks - no event wrapper.
    
    Payload structure:
    {
        "message_type": "incoming" or "outgoing",
        "conversation": {"id": 123, ...},
        "sender": {"phone_number": "+1234567890", "name": "John", ...},
        "content": "Hello",
        "attachments": [{"file_type": "audio", "data_url": "..."}]
    }
    """
    try:
        print(f"=== AGENT BOT MESSAGE HANDLER ===", flush=True)
        
        # Extract data directly from payload (no message wrapper)
        message_type = payload.get('message_type')
        conversation_id = payload.get('conversation', {}).get('id')
        sender = payload.get('sender', {})
        phone_number = sender.get('phone_number')
        sender_name = sender.get('name', 'Customer')
        content = payload.get('content', '')
        attachments = payload.get('attachments', [])
        
        print(f"Message type: {message_type}", flush=True)
        print(f"Conversation ID: {conversation_id}", flush=True)
        print(f"Sender: {sender_name} ({phone_number})", flush=True)
        print(f"Content: {content}", flush=True)
        print(f"Attachments: {len(attachments)} found", flush=True)
        
        # Skip outgoing messages (bot's own messages)
        if message_type == 'outgoing':
            print("SKIPPING: Outgoing message (bot's own message)", flush=True)
            return jsonify({'status': 'skipped_outgoing'})
        
        # Validate required fields
        if not conversation_id:
            print("ERROR: Missing conversation_id", flush=True)
            return jsonify({'error': 'Missing conversation_id'}), 400
        
        # Check for audio attachments and transcribe if found
        is_audio = any(att.get('file_type') == 'audio' for att in attachments)
        if is_audio:
            print("=== AUDIO MESSAGE DETECTED (Agent Bot) ===", flush=True)
            try:
                from app.services.audio_transcriber import AudioTranscriber, extract_audio_from_payload
                
                # Extract audio URL from payload
                audio_url = extract_audio_from_payload(payload)
                print(f"Audio URL: {audio_url[:100] if audio_url else 'None'}...", flush=True)
                
                if audio_url:
                    # Transcribe the audio
                    transcriber = AudioTranscriber()
                    transcribed_text = transcriber.transcribe_from_url(audio_url)
                    
                    if transcribed_text:
                        print(f"Audio transcribed successfully: {transcribed_text[:100]}...", flush=True)
                        content = transcribed_text  # Use transcribed text as content
                    else:
                        print("Audio transcription returned empty text", flush=True)
                        if restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
                            send_chatwoot_response(restaurant, conversation_id, 
                                "I couldn't understand the audio message. Please try again or send a text message.")
                        return jsonify({'status': 'transcription_empty'})
                else:
                    print("Could not extract audio URL from payload", flush=True)
                    if restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
                        send_chatwoot_response(restaurant, conversation_id,
                            "I couldn't process the audio message. Please try again.")
                    return jsonify({'status': 'audio_url_missing'})
                    
            except Exception as audio_error:
                print(f"Audio transcription failed: {audio_error}", flush=True)
                import traceback
                traceback.print_exc()
                if restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
                    send_chatwoot_response(restaurant, conversation_id,
                        "I had trouble processing your voice message. Please try again or send a text message.")
                return jsonify({'status': 'transcription_error', 'error': str(audio_error)})
        
        if not content:
            print("SKIPPING: Empty message content", flush=True)
            return jsonify({'status': 'skipped_empty'})
        
        # Generate AI response
        print(f"=== GENERATING AI RESPONSE ===", flush=True)
        print(f"Customer info - Name: {sender_name}, Phone: {phone_number}", flush=True)
        try:
            # Try using OpenAI Agents SDK first
            try:
                print("Trying OpenAI Agents SDK...", flush=True)
                from app.services.ai_assistant import get_assistant
                # Pass customer info from incoming message for reservation flow
                assistant = get_assistant(
                    restaurant.id, 
                    current_app._get_current_object(),
                    customer_name=sender_name,
                    customer_phone=phone_number
                )
                ai_response = assistant.chat_sync(
                    content, 
                    str(conversation_id), 
                    [],
                    sender_name=sender_name,
                    sender_phone=phone_number
                )
                print(f"AI Response (Agents): {ai_response[:200] if ai_response else 'None'}", flush=True)
            except Exception as agents_error:
                print(f"Agents SDK failed: {agents_error}, using fallback...", flush=True)
                from app.services.ai_assistant_fallback import ReservationAssistantFallback
                assistant = ReservationAssistantFallback(restaurant.id, current_app._get_current_object())
                ai_response = assistant.chat_sync(content, str(conversation_id), [])
                print(f"AI Response (Fallback): {ai_response[:200] if ai_response else 'None'}", flush=True)
            
            # Parse the response if it's JSON (contains buttons)
            try:
                response_data = json.loads(ai_response)
                text_response = response_data.get('text', ai_response)
            except (json.JSONDecodeError, TypeError):
                text_response = ai_response
            
            print(f"Final text response: {text_response[:200] if text_response else 'None'}", flush=True)
            
            # Send response back to Chatwoot
            if restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
                print("Sending response to Chatwoot...", flush=True)
                result = send_chatwoot_response(restaurant, conversation_id, text_response)
                print(f"Send result: {result}", flush=True)
            else:
                print("WARNING: Chatwoot not configured, skipping response", flush=True)
            
            # Log conversation
            conversation = AIConversation(
                restaurant_id=restaurant.id,
                conversation_type='chatwoot',
                transcript=f"User ({sender_name}): {content}\nAI: {text_response}",
                tokens_used=0
            )
            db.session.add(conversation)
            db.session.commit()
            print("Conversation logged to database", flush=True)
            
            return jsonify({'status': 'success', 'response_sent': True})
            
        except Exception as ai_error:
            print(f"AI ERROR: {str(ai_error)}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({'status': 'error', 'error': str(ai_error)}), 500
            
    except Exception as e:
        print(f"HANDLER ERROR: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def handle_chatwoot_message(restaurant, data):
    """
    Handle incoming message from Chatwoot regular webhook (message_created event).
    
    Payload structure for message_created:
    {
        "event": "message_created",
        "content": "message text",  # Content is at root level
        "message_type": 0,  # 0=incoming, 1=outgoing
        "conversation": {"id": 123, "messages": [...]},
        "sender": {...},
        "attachments": [{"file_type": "audio", "data_url": "..."}]
    }
    """
    try:
        print(f"=== HANDLE MESSAGE START ===", flush=True)
        print(f"Full payload: {json.dumps(data, default=str)[:1000]}", flush=True)

        # For message_created event, content and message_type are at ROOT level
        content = data.get('content', '')
        message_type = data.get('message_type')
        conversation_data = data.get('conversation', {})
        sender = data.get('sender', {})
        attachments = data.get('attachments', [])
        
        # If not at root, try getting from conversation.messages[0]
        if not content and conversation_data.get('messages'):
            latest_message = conversation_data['messages'][0]
            content = latest_message.get('content', '')
            message_type = latest_message.get('message_type')
            sender = latest_message.get('sender', sender)
            attachments = latest_message.get('attachments', attachments)
        
        print(f"Content: {content}", flush=True)
        print(f"Message type: {message_type} (type: {type(message_type).__name__})", flush=True)
        print(f"Sender: {sender}", flush=True)
        print(f"Attachments: {len(attachments)} found", flush=True)

        # Chatwoot uses: 0 = incoming, 1 = outgoing, 2 = activity
        # Handle both string and integer message types
        is_incoming = message_type in ['incoming', 0, '0']
        print(f"Is incoming: {is_incoming}", flush=True)

        if not is_incoming:
            print(f"IGNORING: Not an incoming message (type: {message_type})", flush=True)
            return jsonify({'status': 'ignored', 'reason': f'Not an incoming message (type: {message_type})'})

        # Check for audio attachments and transcribe if found
        is_audio = any(att.get('file_type') == 'audio' for att in attachments)
        if is_audio:
            print("=== AUDIO MESSAGE DETECTED ===", flush=True)
            try:
                from app.services.audio_transcriber import AudioTranscriber, extract_audio_from_payload
                
                # Extract audio URL from payload
                audio_url = extract_audio_from_payload(data)
                print(f"Audio URL: {audio_url[:100] if audio_url else 'None'}...", flush=True)
                
                if audio_url:
                    # Transcribe the audio
                    transcriber = AudioTranscriber()
                    transcribed_text = transcriber.transcribe_from_url(audio_url)
                    
                    if transcribed_text:
                        print(f"Audio transcribed successfully: {transcribed_text[:100]}...", flush=True)
                        content = transcribed_text  # Use transcribed text as content
                    else:
                        print("Audio transcription returned empty text", flush=True)
                        # Get conversation ID for error response
                        conversation_id = conversation_data.get('id')
                        if conversation_id and restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
                            send_chatwoot_response(restaurant, conversation_id, 
                                "I couldn't understand the audio message. Please try again or send a text message.")
                        return jsonify({'status': 'transcription_empty'})
                else:
                    print("Could not extract audio URL from payload", flush=True)
                    conversation_id = conversation_data.get('id')
                    if conversation_id and restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
                        send_chatwoot_response(restaurant, conversation_id,
                            "I couldn't process the audio message. Please try again.")
                    return jsonify({'status': 'audio_url_missing'})
                    
            except Exception as audio_error:
                print(f"Audio transcription failed: {audio_error}", flush=True)
                import traceback
                traceback.print_exc()
                conversation_id = conversation_data.get('id')
                if conversation_id and restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
                    send_chatwoot_response(restaurant, conversation_id,
                        "I had trouble processing your voice message. Please try again or send a text message.")
                return jsonify({'status': 'transcription_error', 'error': str(audio_error)})

        if not content:
            print("IGNORING: Empty message content", flush=True)
            return jsonify({'status': 'ignored', 'reason': 'Empty message'})

        # Get conversation ID for session tracking
        conversation_id = conversation_data.get('id')
        print(f"Conversation ID: {conversation_id}", flush=True)

        if not conversation_id:
            print("ERROR: No conversation_id found in payload", flush=True)
            return jsonify({'status': 'error', 'reason': 'No conversation_id'}), 400

        # Get sender info - extract name and phone from sender object
        sender_name = sender.get('name', 'Customer')
        sender_phone = sender.get('phone_number') or sender.get('phone') or sender.get('identifier')
        print(f"Sender name: {sender_name}", flush=True)
        print(f"Sender phone: {sender_phone}", flush=True)

        # Generate AI response
        print(f"=== GENERATING AI RESPONSE ===", flush=True)
        try:
            # Try using OpenAI Agents SDK first
            try:
                print("Trying OpenAI Agents SDK...", flush=True)
                from app.services.ai_assistant import get_assistant
                # Pass customer info from incoming message for reservation flow
                assistant = get_assistant(
                    restaurant.id, 
                    current_app._get_current_object(),
                    customer_name=sender_name,
                    customer_phone=sender_phone
                )
                ai_response = assistant.chat_sync(
                    content, 
                    str(conversation_id), 
                    [],
                    sender_name=sender_name,
                    sender_phone=sender_phone
                )
                print(f"AI Response (Agents): {ai_response[:200]}", flush=True)
            except Exception as agents_error:
                # Fall back to standard OpenAI API
                print(f"Agents SDK failed: {agents_error}, using fallback...", flush=True)
                from app.services.ai_assistant_fallback import ReservationAssistantFallback
                assistant = ReservationAssistantFallback(restaurant.id, current_app._get_current_object())
                ai_response = assistant.chat_sync(content, str(conversation_id), [])
                print(f"AI Response (Fallback): {ai_response[:200]}", flush=True)

            # Parse the response if it's JSON (contains buttons)
            try:
                response_data = json.loads(ai_response)
                text_response = response_data.get('text', ai_response)
            except (json.JSONDecodeError, TypeError):
                text_response = ai_response
            
            print(f"Final text response: {text_response[:200]}", flush=True)

            # Send response back to Chatwoot
            print(f"Chatwoot config - API Key: {bool(restaurant.chatwoot_api_key)}, Base URL: {restaurant.chatwoot_base_url}", flush=True)
            if restaurant.chatwoot_api_key and restaurant.chatwoot_base_url:
                print("Sending response to Chatwoot...", flush=True)
                result = send_chatwoot_response(restaurant, conversation_id, text_response)
                print(f"Send result: {result}", flush=True)
            else:
                print("WARNING: Chatwoot not configured, skipping response", flush=True)

            # Log conversation
            conversation = AIConversation(
                restaurant_id=restaurant.id,
                conversation_type='chatwoot',
                transcript=f"User ({sender_name}): {content}\nAI: {text_response}",
                tokens_used=0
            )
            db.session.add(conversation)
            db.session.commit()
            print("Conversation logged to database", flush=True)

            return jsonify({
                'status': 'success',
                'response_sent': True
            })

        except Exception as ai_error:
            print(f"AI ERROR: {str(ai_error)}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({
                'status': 'error',
                'error': str(ai_error)
            }), 500

    except Exception as e:
        print(f"HANDLER ERROR: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
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

    print(f"=== SENDING TO CHATWOOT ===", flush=True)
    print(f"Conversation ID: {conversation_id}", flush=True)
    print(f"Message: {message[:100]}..." if len(str(message)) > 100 else f"Message: {message}", flush=True)
    print(f"Base URL: {restaurant.chatwoot_base_url}", flush=True)
    print(f"Account ID: {restaurant.chatwoot_account_id}", flush=True)
    print(f"API Key set: {bool(restaurant.chatwoot_api_key)}", flush=True)

    if not all([restaurant.chatwoot_api_key, restaurant.chatwoot_base_url, restaurant.chatwoot_account_id]):
        print(f"ERROR: Chatwoot NOT fully configured for restaurant {restaurant.id}", flush=True)
        return False

    try:
        # Chatwoot API endpoint for sending messages
        base_url = restaurant.chatwoot_base_url.rstrip('/')
        url = f"{base_url}/api/v1/accounts/{restaurant.chatwoot_account_id}/conversations/{conversation_id}/messages"

        print(f"Chatwoot API URL: {url}", flush=True)

        headers = {
            'Content-Type': 'application/json',
            'api_access_token': restaurant.chatwoot_api_key
        }

        payload = {
            'content': message,
            'message_type': 'outgoing',
            'private': False
        }

        print(f"Sending payload: {json.dumps(payload)[:200]}", flush=True)

        response = requests.post(url, json=payload, headers=headers, timeout=10)

        print(f"Chatwoot response status: {response.status_code}", flush=True)
        print(f"Chatwoot response body: {response.text[:500]}", flush=True)

        if response.status_code in [200, 201]:
            print(f"SUCCESS: Message sent to Chatwoot conversation {conversation_id}", flush=True)
            return True
        else:
            print(f"FAILED: Chatwoot API error: {response.status_code} - {response.text}", flush=True)
            return False

    except Exception as e:
        print(f"EXCEPTION sending to Chatwoot: {str(e)}", flush=True)
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
