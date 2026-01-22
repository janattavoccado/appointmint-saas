# AppointMint

**The Future of Table Reservations is Here.**

AppointMint is a voice-enabled AI concierge for restaurant table reservations. Scale your restaurant operations with AI-powered booking via text or natural conversation.

## Features

- **Voice-Enabled AI Assistant**: Natural language processing for voice and text reservations using OpenAI
- **Multi-Tenant Architecture**: Support for multiple restaurant clients with role-based access
- **Smart Table Management**: Intelligent table assignments and scheduling
- **Embeddable Widget**: Copy-paste widget code for any restaurant website
- **Cross-Platform Audio**: Voice input support for web, iOS, and Android
- **Real-Time Analytics**: Track reservations, no-shows, and customer preferences
- **CRM Integration**: Build customer relationships and loyalty
- **Automated Notifications**: SMS and email confirmations

## Role-Based Access Control

AppointMint implements a three-tier role system to ensure proper data isolation and access control:

### 1. System Administrator (`admin`)
The system administrator has **exclusive access** to all system-wide management:
- Manage all tenants (clients)
- Manage all users across all tenants
- View and manage all restaurants across all tenants
- Access system-wide settings and analytics

### 2. Tenant Superuser (`tenant_superuser`)
The tenant superuser has full access to their own organization's data:
- View and edit their organization's information
- Add/edit/delete restaurants for their tenant
- Manage team members (add/edit users within their tenant)
- View reservations and AI conversations for their restaurants

### 3. Tenant User (`tenant_user`)
The tenant user has view access to their organization's data:
- View organization information
- View restaurants belonging to their tenant
- View reservations and AI conversations
- Cannot manage team members or add new users

## Multi-Tenant Workflow

1. **System Admin** creates a new client (tenant)
2. **System Admin** creates a tenant superuser for that client
3. **Tenant Superuser** logs in and manages their organization:
   - Add multiple restaurants
   - Add team members
   - Manage reservations
4. **Tenant Users** can view and work with their organization's data

## Tech Stack

- **Backend**: Flask (Python)
- **Database**: SQLite (development) / PostgreSQL (production)
- **Authentication**: Flask-Login
- **AI**: OpenAI API (GPT-4.1-mini for chat, Whisper for transcription, TTS for voice)
- **Frontend**: HTML, CSS, JavaScript
- **Widget**: Embeddable JavaScript widget with voice support

## Installation

### Prerequisites

- Python 3.8+
- pip

### Setup

1. Extract the project:
```bash
unzip AppointMint.zip
cd AppointMint
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file based on `.env.example`:
```bash
cp .env.example .env
```

5. Edit `.env` and add your configuration:
```
FLASK_APP=run.py
FLASK_ENV=development
SECRET_KEY=your-secret-key-here
OPENAI_API_KEY=your-openai-api-key-here
```

6. Run the application:
```bash
python run.py
```

7. Open your browser and navigate to `http://localhost:5000`

**Note**: If you get database errors after updating, delete the `appointmint.db` file and restart the application to recreate the database with the latest schema.

## Default Admin Account

On first run, a default admin account is created:
- **Email**: admin@appointmint.com
- **Password**: admin123

**Important**: Change this password immediately in production!

## Project Structure

```
AppointMint/
├── app/
│   ├── __init__.py          # Application factory
│   ├── models/
│   │   ├── __init__.py
│   │   └── models.py         # Database models
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── main.py           # Public routes
│   │   ├── auth.py           # Authentication routes
│   │   ├── admin.py          # Admin dashboard routes
│   │   └── api.py            # REST API routes
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css
│   │   ├── js/
│   │   │   └── main.js
│   │   └── images/
│   └── templates/
│       ├── base.html
│       ├── index.html
│       ├── features.html
│       ├── pricing.html
│       ├── about.html
│       ├── demo.html
│       ├── auth/
│       │   ├── login.html
│       │   ├── register.html
│       │   └── forgot_password.html
│       └── admin/
│           ├── base.html
│           ├── dashboard.html
│           ├── restaurants.html
│           ├── restaurant_form.html
│           ├── tables.html
│           ├── table_form.html
│           ├── reservations.html
│           ├── reservation_detail.html
│           ├── users.html
│           ├── user_form.html
│           ├── tenants.html
│           ├── tenant_detail.html
│           ├── tenant_form.html
│           ├── my_organization.html
│           ├── edit_organization.html
│           ├── conversations.html
│           └── settings.html
├── config.py                  # Configuration classes
├── run.py                     # Application entry point
├── requirements.txt           # Python dependencies
├── .env.example              # Environment variables template
├── .gitignore
└── README.md
```

## API Endpoints

### Health Check
```
GET /api/health
```

### Check Availability
```
GET /api/restaurants/<restaurant_id>/availability?date=YYYY-MM-DD&time=HH:MM&party_size=N
```

### Create Reservation
```
POST /api/reservations
Content-Type: application/json

{
    "restaurant_id": 1,
    "customer_name": "John Doe",
    "customer_phone": "+1234567890",
    "customer_email": "john@example.com",
    "party_size": 4,
    "reservation_date": "2025-01-25",
    "reservation_time": "19:00",
    "special_requests": "Window seat preferred"
}
```

### Get Reservation
```
GET /api/reservations/<id>
```

### Update Reservation
```
PUT /api/reservations/<id>
Content-Type: application/json

{
    "status": "confirmed"
}
```

### Cancel Reservation
```
DELETE /api/reservations/<id>
```

### AI Chat
```
POST /api/ai/chat
Content-Type: application/json

{
    "message": "I'd like to make a reservation for 4 people",
    "restaurant_id": 1,
    "session_id": "optional-session-id",
    "conversation_history": [
        {"role": "user", "content": "previous message"},
        {"role": "assistant", "content": "previous response"}
    ]
}
```

### AI Voice Chat
```
POST /api/ai/voice-chat
Content-Type: multipart/form-data

audio: <audio file>
restaurant_id: 1
```

### AI Transcribe
```
POST /api/ai/transcribe
Content-Type: multipart/form-data

audio: <audio file>
```

### AI Text-to-Speech
```
POST /api/ai/speak
Content-Type: application/json

{
    "text": "Your reservation is confirmed.",
    "voice": "alloy"
}
```

## Database Models

| Model | Description |
|-------|-------------|
| **Tenant** | Multi-tenant client (restaurant owner/company) |
| **User** | System users with role-based access |
| **Restaurant** | Restaurant belonging to a tenant |
| **Table** | Tables in a restaurant |
| **OperatingHours** | Operating hours for a restaurant |
| **Reservation** | Table reservations |
| **AIConversation** | Log of AI voice/text conversations |

## User Roles

| Role | Access Level |
|------|--------------|
| `admin` | System-wide access to all tenants and data |
| `tenant_superuser` | Full access to own tenant's data, can manage team |
| `tenant_user` | View access to own tenant's data |

## Configuration

### Development (SQLite)
By default, the application uses SQLite for development. The database file `appointmint.db` is created in the project root.

### Production (PostgreSQL)
For production, set the `DATABASE_URL` environment variable:
```
DATABASE_URL=postgresql://user:password@localhost/appointmint
```

## AI Reservation Assistant

The AI assistant uses OpenAI's GPT-4.1-mini model with function calling to:

1. **Check Availability**: Query the database for available tables
2. **Make Reservations**: Create new reservations with all required details
3. **Get Restaurant Info**: Provide information about the restaurant
4. **Check Reservation Status**: Look up existing reservations
5. **Cancel Reservations**: Cancel existing bookings

### Conversation Flow

The AI follows a natural conversation flow:
1. Greet customer and understand their request
2. Ask for date and time preference
3. Ask for party size
4. Check availability in the database
5. Collect customer name and phone number
6. Ask for email (optional) and special requests
7. Confirm all details
8. Create the reservation and provide confirmation number

### Voice Support

The assistant supports voice input and output:
- **Input**: Uses OpenAI Whisper for speech-to-text transcription
- **Output**: Uses OpenAI TTS for text-to-speech responses
- **Platforms**: Works on web browsers, iOS Safari, and Android Chrome

## Embeddable Widget

Each restaurant can get an embeddable widget to add to their website:

1. Go to **Restaurants** in the admin panel
2. Click on a restaurant name to view details
3. Click **Get Widget Code**
4. Copy the HTML/JavaScript code
5. Paste it into your website before the closing `</body>` tag

### Widget Features

- **Text Chat**: Type messages to make reservations
- **Voice Input**: Click the microphone to speak
- **Audio Responses**: Listen to AI responses
- **Responsive Design**: Works on desktop and mobile
- **Customizable**: Change colors to match your brand

### Widget Configuration

```javascript
AppointMintWidget.init({
    restaurantId: 1,
    apiUrl: 'https://your-domain.com/api',
    theme: {
        primaryColor: '#2DD4BF',
        position: 'bottom-right'  // or 'bottom-left'
    },
    voiceEnabled: true
});
```

## License

MIT License

## Support

For support, email hello@appointmint.com
