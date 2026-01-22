"""
Memory Service using Mem0 for persistent conversation memory.
Provides intelligent memory layer for AI assistant to remember:
- Customer preferences
- Previous reservations
- Conversation context
- Special requests
"""

import os
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    """Structured memory entry"""
    id: str
    memory: str
    user_id: str
    categories: List[str] = []
    created_at: Optional[str] = None
    score: Optional[float] = None
    metadata: Dict[str, Any] = {}


class MemorySearchResult(BaseModel):
    """Search result from memory"""
    results: List[MemoryEntry] = []
    total: int = 0


class ConversationMemory(BaseModel):
    """Memory context for a conversation"""
    user_id: str
    restaurant_id: int
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    preferences: List[str] = []
    past_reservations: List[Dict[str, Any]] = []
    special_notes: List[str] = []
    last_interaction: Optional[str] = None


class MemoryService:
    """
    Memory service using Mem0 API for persistent memory.
    Stores and retrieves customer information, preferences, and conversation context.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize memory service with Mem0 API key"""
        self.api_key = api_key or os.environ.get('MEM0_API_KEY')
        self._client = None
        self._initialized = False
        
        if self.api_key:
            try:
                from mem0 import MemoryClient
                self._client = MemoryClient(api_key=self.api_key)
                self._initialized = True
                print("Mem0 memory service initialized successfully", flush=True)
            except ImportError:
                print("WARNING: mem0ai package not installed. Run: pip install mem0ai", flush=True)
            except Exception as e:
                print(f"WARNING: Failed to initialize Mem0: {e}", flush=True)
        else:
            print("WARNING: MEM0_API_KEY not set. Memory service disabled.", flush=True)
    
    @property
    def is_available(self) -> bool:
        """Check if memory service is available"""
        return self._initialized and self._client is not None
    
    def _get_user_id(self, phone: str, restaurant_id: int) -> str:
        """Generate a unique user ID from phone and restaurant"""
        # Clean phone number
        clean_phone = ''.join(c for c in phone if c.isdigit())
        return f"restaurant_{restaurant_id}_user_{clean_phone}"
    
    def add_memory(
        self,
        messages: List[Dict[str, str]],
        user_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        agent_id: Optional[str] = None
    ) -> bool:
        """
        Add a memory from conversation messages.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            user_id: Unique user identifier
            metadata: Optional metadata to attach
            agent_id: Optional agent identifier for agent-specific memories
        
        Returns:
            True if successful, False otherwise
        """
        if not self.is_available:
            print("Memory service not available, skipping add_memory", flush=True)
            return False
        
        try:
            kwargs = {"user_id": user_id}
            if metadata:
                kwargs["metadata"] = metadata
            if agent_id:
                kwargs["agent_id"] = agent_id
            
            result = self._client.add(messages, **kwargs)
            print(f"Memory added for user {user_id}: {result}", flush=True)
            return True
        except Exception as e:
            print(f"Error adding memory: {e}", flush=True)
            return False
    
    def add_conversation_memory(
        self,
        user_message: str,
        assistant_response: str,
        phone: str,
        restaurant_id: int,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Add a conversation exchange to memory.
        
        Args:
            user_message: The user's message
            assistant_response: The assistant's response
            phone: Customer phone number
            restaurant_id: Restaurant ID
            metadata: Optional metadata
        
        Returns:
            True if successful
        """
        user_id = self._get_user_id(phone, restaurant_id)
        
        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_response}
        ]
        
        # Add context metadata
        full_metadata = {
            "restaurant_id": restaurant_id,
            "phone": phone,
            "timestamp": datetime.utcnow().isoformat(),
            **(metadata or {})
        }
        
        return self.add_memory(messages, user_id, full_metadata)
    
    def add_reservation_memory(
        self,
        phone: str,
        restaurant_id: int,
        reservation_details: Dict[str, Any]
    ) -> bool:
        """
        Store a completed reservation in memory.
        
        Args:
            phone: Customer phone number
            restaurant_id: Restaurant ID
            reservation_details: Dict with date, time, guests, name, special_requests
        """
        user_id = self._get_user_id(phone, restaurant_id)
        
        # Create a natural language summary of the reservation
        date = reservation_details.get('date', 'unknown date')
        time = reservation_details.get('time', 'unknown time')
        guests = reservation_details.get('guests', 'unknown number of')
        name = reservation_details.get('customer_name', 'Customer')
        special = reservation_details.get('special_requests', '')
        
        memory_text = f"Made a reservation for {guests} guests on {date} at {time}."
        if special:
            memory_text += f" Special requests: {special}"
        
        messages = [
            {"role": "user", "content": f"I made a reservation: {memory_text}"},
            {"role": "assistant", "content": f"I've noted your reservation for {date} at {time} for {guests} guests."}
        ]
        
        metadata = {
            "type": "reservation",
            "restaurant_id": restaurant_id,
            **reservation_details
        }
        
        return self.add_memory(messages, user_id, metadata)
    
    def add_preference(
        self,
        phone: str,
        restaurant_id: int,
        preference: str,
        category: str = "general"
    ) -> bool:
        """
        Store a customer preference.
        
        Args:
            phone: Customer phone number
            restaurant_id: Restaurant ID
            preference: The preference to store (e.g., "prefers window seats")
            category: Category of preference (e.g., "seating", "dietary", "timing")
        """
        user_id = self._get_user_id(phone, restaurant_id)
        
        messages = [
            {"role": "user", "content": f"My preference: {preference}"},
            {"role": "assistant", "content": f"I'll remember that you {preference}."}
        ]
        
        metadata = {
            "type": "preference",
            "category": category,
            "restaurant_id": restaurant_id
        }
        
        return self.add_memory(messages, user_id, metadata)
    
    def search_memories(
        self,
        query: str,
        phone: str,
        restaurant_id: int,
        limit: int = 5
    ) -> List[MemoryEntry]:
        """
        Search memories for a user.
        
        Args:
            query: Search query
            phone: Customer phone number
            restaurant_id: Restaurant ID
            limit: Maximum number of results
        
        Returns:
            List of matching memory entries
        """
        if not self.is_available:
            return []
        
        user_id = self._get_user_id(phone, restaurant_id)
        
        try:
            results = self._client.search(
                query,
                filters={"user_id": user_id},
                limit=limit
            )
            
            memories = []
            for item in results.get('results', []):
                memories.append(MemoryEntry(
                    id=item.get('id', ''),
                    memory=item.get('memory', ''),
                    user_id=item.get('user_id', user_id),
                    categories=item.get('categories', []),
                    created_at=item.get('created_at'),
                    score=item.get('score'),
                    metadata=item.get('metadata', {})
                ))
            
            print(f"Found {len(memories)} memories for query '{query}'", flush=True)
            return memories
        except Exception as e:
            print(f"Error searching memories: {e}", flush=True)
            return []
    
    def get_customer_context(
        self,
        phone: str,
        restaurant_id: int
    ) -> ConversationMemory:
        """
        Get full customer context from memory.
        
        Args:
            phone: Customer phone number
            restaurant_id: Restaurant ID
        
        Returns:
            ConversationMemory with all known information about the customer
        """
        user_id = self._get_user_id(phone, restaurant_id)
        
        context = ConversationMemory(
            user_id=user_id,
            restaurant_id=restaurant_id,
            customer_phone=phone
        )
        
        if not self.is_available:
            return context
        
        try:
            # Search for customer name
            name_results = self.search_memories("customer name", phone, restaurant_id, limit=3)
            for mem in name_results:
                if 'name' in mem.metadata:
                    context.customer_name = mem.metadata['name']
                    break
            
            # Search for preferences
            pref_results = self.search_memories("preferences seating dietary", phone, restaurant_id, limit=5)
            for mem in pref_results:
                if mem.memory and mem.memory not in context.preferences:
                    context.preferences.append(mem.memory)
            
            # Search for past reservations
            res_results = self.search_memories("reservation booking", phone, restaurant_id, limit=5)
            for mem in res_results:
                if mem.metadata.get('type') == 'reservation':
                    context.past_reservations.append(mem.metadata)
            
            # Get last interaction time
            all_results = self._client.get_all(user_id=user_id, limit=1)
            if all_results and all_results.get('results'):
                context.last_interaction = all_results['results'][0].get('created_at')
            
            print(f"Retrieved customer context for {phone}: name={context.customer_name}, "
                  f"{len(context.preferences)} preferences, {len(context.past_reservations)} past reservations", 
                  flush=True)
            
        except Exception as e:
            print(f"Error getting customer context: {e}", flush=True)
        
        return context
    
    def get_all_memories(
        self,
        phone: str,
        restaurant_id: int,
        limit: int = 20
    ) -> List[MemoryEntry]:
        """
        Get all memories for a user.
        
        Args:
            phone: Customer phone number
            restaurant_id: Restaurant ID
            limit: Maximum number of results
        
        Returns:
            List of all memory entries
        """
        if not self.is_available:
            return []
        
        user_id = self._get_user_id(phone, restaurant_id)
        
        try:
            results = self._client.get_all(user_id=user_id, limit=limit)
            
            memories = []
            for item in results.get('results', []):
                memories.append(MemoryEntry(
                    id=item.get('id', ''),
                    memory=item.get('memory', ''),
                    user_id=item.get('user_id', user_id),
                    categories=item.get('categories', []),
                    created_at=item.get('created_at'),
                    metadata=item.get('metadata', {})
                ))
            
            return memories
        except Exception as e:
            print(f"Error getting all memories: {e}", flush=True)
            return []
    
    def delete_memory(self, memory_id: str) -> bool:
        """Delete a specific memory by ID"""
        if not self.is_available:
            return False
        
        try:
            self._client.delete(memory_id)
            print(f"Deleted memory {memory_id}", flush=True)
            return True
        except Exception as e:
            print(f"Error deleting memory: {e}", flush=True)
            return False
    
    def clear_user_memories(self, phone: str, restaurant_id: int) -> bool:
        """Clear all memories for a user"""
        if not self.is_available:
            return False
        
        user_id = self._get_user_id(phone, restaurant_id)
        
        try:
            self._client.delete_all(user_id=user_id)
            print(f"Cleared all memories for user {user_id}", flush=True)
            return True
        except Exception as e:
            print(f"Error clearing memories: {e}", flush=True)
            return False


# Singleton instance
_memory_service: Optional[MemoryService] = None


def get_memory_service(api_key: Optional[str] = None) -> MemoryService:
    """Get or create the memory service singleton"""
    global _memory_service
    
    if _memory_service is None:
        _memory_service = MemoryService(api_key)
    
    return _memory_service


def format_memories_for_context(memories: List[MemoryEntry], max_chars: int = 1000) -> str:
    """
    Format memories into a context string for the AI prompt.
    
    Args:
        memories: List of memory entries
        max_chars: Maximum characters for the context
    
    Returns:
        Formatted string of memories
    """
    if not memories:
        return ""
    
    lines = ["Previous interactions and preferences:"]
    total_chars = len(lines[0])
    
    for mem in memories:
        line = f"- {mem.memory}"
        if total_chars + len(line) > max_chars:
            break
        lines.append(line)
        total_chars += len(line)
    
    return "\n".join(lines)
