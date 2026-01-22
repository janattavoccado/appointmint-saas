/**
 * AppointMint Reservation Widget
 * Embeddable AI-powered chat widget for restaurant websites
 * Supports text and voice input (text-only responses)
 */

(function() {
    'use strict';

    // Widget namespace
    window.AppointMintWidget = window.AppointMintWidget || {};

    // Default configuration
    const defaultConfig = {
        restaurantId: null,
        apiUrl: '',
        theme: {
            primaryColor: '#4CAF50',
            position: 'bottom-right'
        },
        welcomeMessage: null,
        voiceEnabled: true
    };

    // State
    let config = {};
    let isOpen = false;
    let isRecording = false;
    let mediaRecorder = null;
    let audioChunks = [];
    let sessionId = null;
    let conversationHistory = [];

    // Generate session ID
    function generateSessionId() {
        return 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }

    // Initialize widget
    AppointMintWidget.init = function(userConfig) {
        config = { ...defaultConfig, ...userConfig };
        
        if (!config.restaurantId) {
            console.error('AppointMint Widget: restaurantId is required');
            return;
        }

        sessionId = generateSessionId();
        
        // Load widget config from server
        fetchWidgetConfig().then(() => {
            createWidget();
            attachEventListeners();
        });
    };

    // Fetch widget configuration
    async function fetchWidgetConfig() {
        try {
            const response = await fetch(`${config.apiUrl}/widget/${config.restaurantId}/config`);
            if (response.ok) {
                const data = await response.json();
                if (!config.welcomeMessage) {
                    config.welcomeMessage = data.welcome_message;
                }
                config.restaurantName = data.restaurant_name;
            }
        } catch (error) {
            console.error('AppointMint Widget: Failed to fetch config', error);
        }
    }

    // Create widget HTML
    function createWidget() {
        const position = config.theme.position || 'bottom-right';
        
        const widgetHTML = `
            <div class="appointmint-widget ${position}" id="appointmint-widget">
                <!-- Toggle Button with chat/close icons -->
                <button class="appointmint-toggle" id="appointmint-toggle" aria-label="Open chat">
                    <svg class="chat-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/>
                        <path d="M7 9h10v2H7zm0-3h10v2H7z"/>
                    </svg>
                    <svg class="close-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                    </svg>
                </button>

                <!-- Chat Window -->
                <div class="appointmint-chat" id="appointmint-chat">
                    <!-- Header -->
                    <div class="appointmint-header">
                        <div class="appointmint-avatar">
                            <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
                            </svg>
                        </div>
                        <div class="appointmint-header-info">
                            <h3 class="appointmint-header-title">Book at ${config.restaurantName || 'Restaurant'}</h3>
                        </div>
                        <button class="appointmint-close" id="appointmint-close" aria-label="Close chat">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                            </svg>
                        </button>
                    </div>

                    <!-- Messages -->
                    <div class="appointmint-messages" id="appointmint-messages">
                        <!-- Messages will be inserted here -->
                    </div>

                    <!-- Recording Indicator -->
                    <div class="appointmint-recording-indicator" id="appointmint-recording-indicator">
                        <span class="appointmint-recording-dot"></span>
                        <span>Recording... Tap to stop</span>
                    </div>

                    <!-- Input Area -->
                    <div class="appointmint-input-area">
                        <div class="appointmint-input-wrapper">
                            <textarea 
                                class="appointmint-input" 
                                id="appointmint-input" 
                                placeholder="Type your message..."
                                rows="1"
                            ></textarea>
                        </div>
                        ${config.voiceEnabled ? `
                        <button class="appointmint-voice" id="appointmint-voice" aria-label="Voice input">
                            <svg viewBox="0 0 24 24">
                                <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5z"/>
                                <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
                            </svg>
                        </button>
                        ` : ''}
                        <button class="appointmint-send" id="appointmint-send" aria-label="Send message">
                            <svg viewBox="0 0 24 24">
                                <path d="M8 5v14l11-7z"/>
                            </svg>
                        </button>
                    </div>

                    <!-- Powered By -->
                    <div class="appointmint-powered">
                        Powered by <a href="https://appointmint.com" target="_blank" rel="noopener">AppointMint</a>
                    </div>
                </div>
            </div>
        `;

        // Insert widget into page
        const container = document.getElementById('appointmint-widget') || document.body;
        if (container === document.body) {
            container.insertAdjacentHTML('beforeend', widgetHTML);
        } else {
            container.outerHTML = widgetHTML;
        }

        // Apply custom theme color
        if (config.theme.primaryColor) {
            document.documentElement.style.setProperty('--appointmint-primary', config.theme.primaryColor);
            // Generate darker shade for hover
            const darkerColor = adjustColor(config.theme.primaryColor, -20);
            document.documentElement.style.setProperty('--appointmint-primary-dark', darkerColor);
            // Generate lighter shade
            const lighterColor = adjustColor(config.theme.primaryColor, 30);
            document.documentElement.style.setProperty('--appointmint-primary-light', lighterColor);
        }
    }

    // Adjust color brightness
    function adjustColor(color, amount) {
        const hex = color.replace('#', '');
        const r = Math.max(0, Math.min(255, parseInt(hex.substr(0, 2), 16) + amount));
        const g = Math.max(0, Math.min(255, parseInt(hex.substr(2, 2), 16) + amount));
        const b = Math.max(0, Math.min(255, parseInt(hex.substr(4, 2), 16) + amount));
        return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
    }

    // Attach event listeners
    function attachEventListeners() {
        // Toggle button
        document.getElementById('appointmint-toggle').addEventListener('click', toggleChat);
        
        // Close button
        document.getElementById('appointmint-close').addEventListener('click', closeChat);
        
        // Send button
        document.getElementById('appointmint-send').addEventListener('click', sendMessage);
        
        // Input field
        const input = document.getElementById('appointmint-input');
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        
        // Auto-resize textarea
        input.addEventListener('input', () => {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 80) + 'px';
        });
        
        // Voice button
        const voiceBtn = document.getElementById('appointmint-voice');
        if (voiceBtn) {
            voiceBtn.addEventListener('click', toggleRecording);
        }
    }

    // Toggle chat window
    function toggleChat() {
        isOpen = !isOpen;
        const chat = document.getElementById('appointmint-chat');
        const toggle = document.getElementById('appointmint-toggle');
        
        if (isOpen) {
            chat.classList.add('open');
            toggle.classList.add('open');
            
            // Show welcome message if first time
            const messages = document.getElementById('appointmint-messages');
            if (messages.children.length === 0 && config.welcomeMessage) {
                addMessage(config.welcomeMessage, 'assistant');
            }
            
            // Focus input
            setTimeout(() => {
                document.getElementById('appointmint-input').focus();
            }, 300);
        } else {
            chat.classList.remove('open');
            toggle.classList.remove('open');
        }
    }

    // Close chat
    function closeChat() {
        isOpen = false;
        document.getElementById('appointmint-chat').classList.remove('open');
        document.getElementById('appointmint-toggle').classList.remove('open');
    }

    // Add message to chat
    function addMessage(text, type) {
        const messages = document.getElementById('appointmint-messages');
        const messageDiv = document.createElement('div');
        messageDiv.className = `appointmint-message ${type}`;
        
        // Format text with line breaks and basic markdown
        let formattedText = text
            .replace(/\n/g, '<br>')
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/✅/g, '<span style="color:#4CAF50">✅</span>')
            .replace(/📋/g, '<span>📋</span>')
            .replace(/📌/g, '<span style="color:#f44336">📌</span>')
            .replace(/📅/g, '<span style="color:#2196F3">📅</span>')
            .replace(/⏰/g, '<span>⏰</span>')
            .replace(/👥/g, '<span>👥</span>')
            .replace(/👤/g, '<span>👤</span>')
            .replace(/📞/g, '<span style="color:#4CAF50">📞</span>')
            .replace(/📝/g, '<span style="color:#FF9800">📝</span>');
        
        messageDiv.innerHTML = formattedText;
        
        messages.appendChild(messageDiv);
        messages.scrollTop = messages.scrollHeight;
    }

    // Show typing indicator
    function showTyping() {
        const messages = document.getElementById('appointmint-messages');
        const typing = document.createElement('div');
        typing.className = 'appointmint-typing';
        typing.id = 'appointmint-typing';
        typing.innerHTML = '<span></span><span></span><span></span>';
        messages.appendChild(typing);
        messages.scrollTop = messages.scrollHeight;
    }

    // Hide typing indicator
    function hideTyping() {
        const typing = document.getElementById('appointmint-typing');
        if (typing) {
            typing.remove();
        }
    }

    // Send text message
    async function sendMessage() {
        const input = document.getElementById('appointmint-input');
        const message = input.value.trim();
        
        if (!message) return;
        
        // Clear input
        input.value = '';
        input.style.height = 'auto';
        
        // Add user message
        addMessage(message, 'user');
        
        // Add to conversation history
        conversationHistory.push({ role: 'user', content: message });
        
        // Show typing indicator
        showTyping();
        
        try {
            const response = await fetch(`${config.apiUrl}/ai/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    message: message,
                    restaurant_id: config.restaurantId,
                    session_id: sessionId,
                    conversation_history: conversationHistory.slice(0, -1)
                })
            });
            
            hideTyping();
            
            if (response.ok) {
                const data = await response.json();
                addMessage(data.response, 'assistant');
                
                // Add assistant response to conversation history
                conversationHistory.push({ role: 'assistant', content: data.response });
                
                // Keep conversation history manageable (last 20 messages)
                if (conversationHistory.length > 20) {
                    conversationHistory = conversationHistory.slice(-20);
                }
            } else {
                const error = await response.json();
                addMessage('Sorry, I encountered an error. Please try again.', 'system');
                console.error('Chat error:', error);
                conversationHistory.pop();
            }
        } catch (error) {
            hideTyping();
            addMessage('Sorry, I couldn\'t connect to the server. Please try again.', 'system');
            console.error('Network error:', error);
            conversationHistory.pop();
        }
    }

    // Toggle voice recording
    async function toggleRecording() {
        if (isRecording) {
            stopRecording();
        } else {
            await startRecording();
        }
    }

    // Start voice recording
    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            
            // Determine best supported format
            let mimeType = 'audio/webm';
            if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                mimeType = 'audio/webm;codecs=opus';
            } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
                mimeType = 'audio/mp4';
            } else if (MediaRecorder.isTypeSupported('audio/ogg')) {
                mimeType = 'audio/ogg';
            }
            
            mediaRecorder = new MediaRecorder(stream, { mimeType });
            audioChunks = [];
            
            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    audioChunks.push(event.data);
                }
            };
            
            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: mimeType });
                await sendVoiceMessage(audioBlob);
                
                // Stop all tracks
                stream.getTracks().forEach(track => track.stop());
            };
            
            mediaRecorder.start();
            isRecording = true;
            
            // Update UI
            document.getElementById('appointmint-voice').classList.add('recording');
            document.getElementById('appointmint-recording-indicator').classList.add('active');
            
        } catch (error) {
            console.error('Failed to start recording:', error);
            addMessage('Could not access microphone. Please check permissions.', 'system');
        }
    }

    // Stop voice recording
    function stopRecording() {
        if (mediaRecorder && isRecording) {
            mediaRecorder.stop();
            isRecording = false;
            
            // Update UI
            document.getElementById('appointmint-voice').classList.remove('recording');
            document.getElementById('appointmint-recording-indicator').classList.remove('active');
        }
    }

    // Send voice message (transcribed, AI responds with text only)
    async function sendVoiceMessage(audioBlob) {
        // Add user message indicator
        addMessage('🎤 Voice message sent', 'user');
        
        // Show typing indicator
        showTyping();
        
        try {
            const formData = new FormData();
            formData.append('audio', audioBlob, 'recording.webm');
            formData.append('restaurant_id', config.restaurantId);
            formData.append('session_id', sessionId);
            formData.append('conversation_history', JSON.stringify(conversationHistory));
            
            const response = await fetch(`${config.apiUrl}/ai/voice-chat`, {
                method: 'POST',
                body: formData
            });
            
            hideTyping();
            
            if (response.ok) {
                const data = await response.json();
                
                // Show transcription - update the voice message with actual text
                if (data.user_text) {
                    const messages = document.getElementById('appointmint-messages');
                    const lastUserMsg = messages.querySelector('.appointmint-message.user:last-of-type');
                    if (lastUserMsg && lastUserMsg.textContent.includes('Voice message')) {
                        lastUserMsg.innerHTML = `🎤 "${data.user_text}"`;
                    }
                    // Add to conversation history
                    conversationHistory.push({ role: 'user', content: data.user_text });
                }
                
                // Add AI text response (no audio - AI only responds with text)
                addMessage(data.ai_response, 'assistant');
                
                // Add assistant response to conversation history
                conversationHistory.push({ role: 'assistant', content: data.ai_response });
                
                // Keep conversation history manageable
                if (conversationHistory.length > 20) {
                    conversationHistory = conversationHistory.slice(-20);
                }
            } else {
                const error = await response.json();
                addMessage('Sorry, I couldn\'t process your voice message. Please try again.', 'system');
                console.error('Voice chat error:', error);
            }
        } catch (error) {
            hideTyping();
            addMessage('Sorry, I couldn\'t connect to the server. Please try again.', 'system');
            console.error('Network error:', error);
        }
    }

    // Public method to open chat
    AppointMintWidget.open = function() {
        if (!isOpen) {
            toggleChat();
        }
    };

    // Public method to close chat
    AppointMintWidget.close = function() {
        if (isOpen) {
            closeChat();
        }
    };

    // Public method to send a message programmatically
    AppointMintWidget.sendMessage = function(message) {
        const input = document.getElementById('appointmint-input');
        input.value = message;
        sendMessage();
    };

})();
