'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { Sidebar } from '@/components/Sidebar'
import { ChatArea, Message } from '@/components/ChatArea'
import { InputBar } from '@/components/InputBar'
import { PluginsDashboard } from '@/components/PluginsDashboard'
import { useVoice } from '@/hooks/useVoice'
import { useUpload, UploadResult } from '@/hooks/useUpload'
import { Loader2 } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000'

interface Conversation {
    id: string
    title: string
    updated_at: string
}

export default function Home() {
    // State
    const [messages, setMessages] = useState<Message[]>([])
    const [conversations, setConversations] = useState<Conversation[]>([])
    const [conversationId, setConversationId] = useState<string | null>(null)
    const [isLoading, setIsLoading] = useState(false)
    const [statusMessage, setStatusMessage] = useState<string>('')
    const [pendingAttachments, setPendingAttachments] = useState<UploadResult[]>([])
    const [activeTab, setActiveTab] = useState<'chats' | 'plugins'>('chats')

    // Hooks
    const { uploadFile, isUploading } = useUpload()

    const onVoiceTranscription = (text: string) => {
        setMessages(prev => [...prev, { role: 'user', content: text }])
    }

    const onVoiceResponse = (text: string) => {
        setMessages(prev => [...prev, { role: 'assistant', content: text }])
    }

    const { isRecording, toggleVoice } = useVoice(onVoiceTranscription, onVoiceResponse)

    // WebSocket for Chat Streaming (Text Mode)
    const wsRef = useRef<WebSocket | null>(null)
    const fullContentRef = useRef('')

    // Fetch Conversations
    const fetchConversations = useCallback(async () => {
        try {
            const res = await fetch(`${API_URL}/api/conversations`)
            if (res.ok) {
                const data = await res.json()
                setConversations(data)
            }
        } catch (err) {
            console.error('Failed to fetch conversations', err)
        }
    }, [])

    useEffect(() => {
        fetchConversations()
    }, [fetchConversations])

    // Load Conversation
    const loadConversation = async (id: string) => {
        try {
            const res = await fetch(`${API_URL}/api/conversations/${id}`)
            if (res.ok) {
                const data = await res.json()
                setMessages(data) // Backend returns array of messages
                setConversationId(id)
                setActiveTab('chats')
            }
        } catch (err) {
            console.error('Failed to load conversation', err)
        }
    }

    const startNewChat = () => {
        setConversationId(null)
        setMessages([])
        setPendingAttachments([])
    }

    const deleteConversation = async (id: string, e: React.MouseEvent) => {
        e.stopPropagation()
        if (!confirm('Are you sure you want to delete this chat?')) return
        try {
            await fetch(`${API_URL}/api/conversations/${id}`, { method: 'DELETE' })
            setConversations(prev => prev.filter(c => c.id !== id))
            if (conversationId === id) startNewChat()
        } catch (err) {
            console.error('Failed to delete', err)
        }
    }

    // Chat Logic
    const sendMessage = async (content: string) => {
        if (!content.trim() && pendingAttachments.length === 0) return

        setIsLoading(true)
        setStatusMessage('Thinking...')

        // Optimistic update
        const userMsg: Message = { role: 'user', content }
        setMessages(prev => [...prev, userMsg])

        try {
            // Re-connect WS if needed
            if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
                wsRef.current = new WebSocket(`${WS_URL}/api/chat/stream`)
                await new Promise(resolve => {
                    if (wsRef.current) wsRef.current.onopen = resolve
                })
            }

            const payload = {
                messages: [...messages, {
                    ...userMsg,
                    attachments: pendingAttachments.map(att => ({
                        id: att.id,
                        type: att.type,
                        base64_data: (att as any).base64_data
                    }))
                }],
                conversation_id: conversationId,
                model: undefined // Let backend use default setting
            }

            wsRef.current?.send(JSON.stringify(payload))

            // Reset pending
            setPendingAttachments([])

            // Handle Stream
            if (wsRef.current) {
                wsRef.current.onmessage = (event) => {
                    const data = JSON.parse(event.data)

                    // Handle Status Updates
                    if (data.status) {
                        setStatusMessage(data.status)
                    }

                    // Handle Tool Calls (Clear raw JSON and set tool calls)
                    if (data.tool_calls) {
                        fullContentRef.current = '' // Reset content buffer
                        setStatusMessage('Executing tools...')
                        setMessages(prev => {
                            const last = prev[prev.length - 1]
                            if (last && last.role === 'assistant') {
                                return [...prev.slice(0, -1), {
                                    ...last,
                                    content: '', // Clear the raw JSON
                                    tool_calls: data.tool_calls
                                }]
                            }
                            return prev
                        })
                    }

                    if (data.content) {
                        fullContentRef.current += data.content
                        // Update last message (streaming)
                        setMessages(prev => {
                            const last = prev[prev.length - 1]

                            // If last message acts as a tool carrier, start a new text bubble
                            if (last && last.role === 'assistant' && last.tool_calls && last.tool_calls.length > 0) {
                                return [...prev, { role: 'assistant', content: fullContentRef.current }]
                            }

                            // Normal streaming (append to last)
                            if (last && last.role === 'assistant') {
                                return [...prev.slice(0, -1), { ...last, content: fullContentRef.current }]
                            } else {
                                return [...prev, { role: 'assistant', content: fullContentRef.current }]
                            }
                        })
                    }

                    if (data.done) {
                        fullContentRef.current = ''
                        setIsLoading(false)
                        setStatusMessage('')
                        if (data.conversation_id) {
                            setConversationId(data.conversation_id)
                            fetchConversations() // Refresh list
                        }
                    }
                    if (data.error) {
                        console.error(data.error)
                        setIsLoading(false)
                        setStatusMessage('')
                    }
                }
            }

        } catch (err) {
            console.error(err)
            setIsLoading(false)
        }
    }

    const handleUpload = async (file: File) => {
        const result = await uploadFile(file)
        if (result) {
            setPendingAttachments(prev => [...prev, result])
            // Show a temporary system message or notification
            setMessages(prev => [...prev, {
                role: 'system',
                content: `Uploaded: ${file.name}`
            }])
        }
    }

    return (
        <div className="flex h-screen bg-transparent text-brand-dark font-sans overflow-hidden">
            {/* Sidebar */}
            <Sidebar
                conversations={conversations}
                currentId={conversationId || ''}
                onSelect={loadConversation}
                onNew={() => { startNewChat(); setActiveTab('chats'); }}
                onDelete={deleteConversation}
                onPluginsClick={() => setActiveTab('plugins')}
                onChatsClick={() => setActiveTab('chats')}
                activeTab={activeTab}
            />

            {/* Main Content */}
            <div className="flex-1 flex flex-col min-w-0 relative">
                {activeTab === 'chats' ? (
                    <>
                        <ChatArea messages={messages} isLoading={isLoading} statusMessage={statusMessage} />

                        {/* Input Area */}
                        <div className="p-4 bg-transparent">
                            {pendingAttachments.length > 0 && (
                                <div className="text-xs text-brand-pink mb-2 px-4 font-bold">
                                    {pendingAttachments.length} file(s) attached
                                </div>
                            )}
                            <InputBar
                                onSend={sendMessage}
                                onUpload={handleUpload}
                                onVoiceToggle={toggleVoice}
                                isRecording={isRecording}
                                isLoading={isLoading || isUploading}
                            />
                        </div>
                    </>
                ) : (
                    <PluginsDashboard />
                )}
            </div>
        </div>
    )
}
