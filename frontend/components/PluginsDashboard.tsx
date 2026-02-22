'use client'

import React, { useState, useEffect, useCallback } from 'react'
import {
    Cpu, Plus, Server, Wrench, CheckCircle2, XCircle,
    Loader2, ChevronRight, Radio, Box, Zap, X, AlertTriangle
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

// ─── Types ───
interface MCPServer {
    name: string
    command: string
    args: string[]
    status: string
}

interface PluginStatus {
    active_model: string
    active_provider: string
    mcp_servers: MCPServer[]
    tool_count: number
    tools: string[]
}

interface AddModelResult {
    success: boolean
    message: string
    connectivity_ok: boolean
    tools_ok: boolean
    streaming_ok: boolean
    details: string[]
}

// ─── Component ───
export const PluginsDashboard: React.FC = () => {
    const [status, setStatus] = useState<PluginStatus | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    // Add/Edit Model Modal
    const [showAddModal, setShowAddModal] = useState(false)
    const [isEditing, setIsEditing] = useState(false)
    const [modalStep, setModalStep] = useState<1 | 2 | 3>(1)
    const [modelType, setModelType] = useState<'open_source' | 'paid' | null>(null)

    // Form fields
    const [formProvider, setFormProvider] = useState('ollama')
    const [formModel, setFormModel] = useState('')
    const [formApiKey, setFormApiKey] = useState('')
    const [formBaseUrl, setFormBaseUrl] = useState('http://localhost:11434')

    // Verification
    const [verifying, setVerifying] = useState(false)
    const [verifyResult, setVerifyResult] = useState<AddModelResult | null>(null)

    const fetchStatus = useCallback(async () => {
        try {
            setLoading(true)
            const res = await fetch(`${API_URL}/api/plugins/status`)
            if (!res.ok) throw new Error(`Status ${res.status}`)
            const data = await res.json()
            setStatus(data)
            setError(null)
        } catch (e: any) {
            setError(e.message || 'Failed to load plugin status')
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { fetchStatus() }, [fetchStatus])

    const resetModal = () => {
        setShowAddModal(false)
        setIsEditing(false)
        setModalStep(1)
        setModelType(null)
        setFormProvider('ollama')
        setFormModel('')
        setFormApiKey('')
        setFormBaseUrl('http://localhost:11434')
        setVerifying(false)
        setVerifyResult(null)
    }

    const startEdit = () => {
        if (!status) return
        setIsEditing(true)
        setModelType(status.active_provider === 'ollama' ? 'open_source' : 'paid')
        setFormProvider(status.active_provider)
        setFormModel(status.active_model)
        // Note: API Key and Base URL are not retrieved from status, we keep defaults or empty
        setModalStep(2)
        setShowAddModal(true)
    }

    const handleAddModel = async () => {
        setVerifying(true)
        setVerifyResult(null)
        setModalStep(3)

        try {
            const res = await fetch(`${API_URL}/api/plugins/add-model`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    type: modelType,
                    provider: formProvider,
                    model: formModel,
                    api_key: formApiKey || undefined,
                    base_url: modelType === 'open_source' ? formBaseUrl : undefined,
                }),
            })
            const data: AddModelResult = await res.json()
            setVerifyResult(data)

            if (data.success) {
                // Refresh status after successful add
                await fetchStatus()
            }
        } catch (e: any) {
            setVerifyResult({
                success: false,
                message: e.message || 'Network error',
                connectivity_ok: false,
                tools_ok: false,
                streaming_ok: false,
                details: [e.message],
            })
        } finally {
            setVerifying(false)
        }
    }

    // ─── Render ───
    if (loading && !status) {
        return (
            <div className="flex-1 flex items-center justify-center">
                <Loader2 className="w-8 h-8 text-brand-accent animate-spin" />
            </div>
        )
    }

    return (
        <div className="flex-1 overflow-y-auto p-6 md:p-8">
            {/* Header */}
            <div className="mb-8">
                <h2 className="text-2xl font-bold text-white mb-1">Plugins & Configuration</h2>
                <p className="text-brand-grey text-sm">Manage your AI models, MCP servers, and tools.</p>
            </div>

            {error && (
                <div className="mb-6 p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0" />
                    {error}
                </div>
            )}

            {/* Active Model Card */}
            <div className="mb-8 p-6 rounded-2xl bg-brand-surface border border-white/5 relative overflow-hidden">
                <div className="absolute inset-0 bg-gradient-to-br from-brand-accent/5 to-transparent pointer-events-none" />
                <div className="relative flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <div className="w-12 h-12 rounded-xl bg-brand-accent/10 flex items-center justify-center">
                            <Cpu className="w-6 h-6 text-brand-accent" />
                        </div>
                        <div>
                            <div className="text-[11px] uppercase tracking-widest text-brand-grey font-bold mb-1">Active Model</div>
                            <div className="text-xl font-bold text-white">{status?.active_model || '—'}</div>
                            <div className="text-xs text-brand-grey mt-0.5">
                                Provider: <span className="text-brand-lighter">{status?.active_provider || '—'}</span>
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <button
                            onClick={startEdit}
                            className="p-2.5 rounded-xl bg-white/5 hover:bg-white/10 text-brand-grey hover:text-white transition-all cursor-pointer group relative"
                            title="Edit Current Model"
                        >
                            <Wrench className="w-5 h-5" />
                            <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 px-2 py-1 bg-brand-surface border border-white/10 rounded text-[10px] opacity-0 group-hover:opacity-100 pointer-events-none whitespace-nowrap">
                                Edit Configuration
                            </div>
                        </button>
                        <button
                            onClick={() => { setIsEditing(false); setShowAddModal(true) }}
                            className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-brand-accent/10 hover:bg-brand-accent/20 text-brand-accent text-sm font-medium transition-all hover:shadow-glow cursor-pointer"
                        >
                            <Plus className="w-4 h-4" />
                            Add New Model
                        </button>
                    </div>
                </div>
            </div>

            {/* Two Column Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* MCP Servers */}
                <div className="p-6 rounded-2xl bg-brand-surface border border-white/5">
                    <div className="flex items-center gap-2 mb-4">
                        <Server className="w-5 h-5 text-brand-accent" />
                        <h3 className="text-lg font-semibold text-white">MCP Servers</h3>
                        <span className="ml-auto text-xs text-brand-grey bg-white/5 px-2 py-0.5 rounded-full">
                            {status?.mcp_servers?.length || 0}
                        </span>
                    </div>
                    <div className="space-y-2 max-h-80 overflow-y-auto">
                        {status?.mcp_servers?.map((server) => (
                            <div
                                key={server.name}
                                className="flex items-center gap-3 p-3 rounded-xl bg-white/[0.02] hover:bg-white/[0.05] transition-colors group"
                            >
                                <div className="w-2 h-2 rounded-full bg-green-400 shadow-[0_0_6px_rgba(74,222,128,0.4)]" />
                                <span className="text-sm text-white font-medium flex-1 truncate">{server.name}</span>
                                <span className="text-[10px] text-brand-grey font-mono">{server.command}</span>
                            </div>
                        ))}
                        {(!status?.mcp_servers || status.mcp_servers.length === 0) && (
                            <div className="text-sm text-brand-grey text-center py-4">No MCP servers configured</div>
                        )}
                    </div>
                </div>

                {/* Tools Inventory */}
                <div className="p-6 rounded-2xl bg-brand-surface border border-white/5">
                    <div className="flex items-center gap-2 mb-4">
                        <Wrench className="w-5 h-5 text-brand-accent" />
                        <h3 className="text-lg font-semibold text-white">Available Tools</h3>
                        <span className="ml-auto text-xs text-brand-grey bg-white/5 px-2 py-0.5 rounded-full">
                            {status?.tool_count || 0}
                        </span>
                    </div>
                    <div className="space-y-1.5 max-h-80 overflow-y-auto">
                        {status?.tools?.map((tool) => (
                            <div
                                key={tool}
                                className="flex items-center gap-3 px-3 py-2 rounded-lg bg-white/[0.02] hover:bg-white/[0.05] transition-colors"
                            >
                                <Zap className="w-3.5 h-3.5 text-brand-lighter" />
                                <span className="text-sm text-gray-300 font-mono">{tool}</span>
                            </div>
                        ))}
                        {(!status?.tools || status.tools.length === 0) && (
                            <div className="text-sm text-brand-grey text-center py-4">No tools registered</div>
                        )}
                    </div>
                </div>
            </div>

            {/* ─── ADD MODEL MODAL ─── */}
            {showAddModal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
                    <div className="w-full max-w-lg mx-4 rounded-2xl bg-brand-surface border border-white/10 shadow-2xl overflow-hidden">
                        {/* Modal Header */}
                        <div className="px-6 py-4 border-b border-white/5 flex items-center justify-between">
                            <h3 className="text-lg font-bold text-white">
                                {isEditing ? 'Edit Model Configuration' : 'Add New Model'}
                            </h3>
                            <button
                                onClick={resetModal}
                                className="p-1 rounded-lg hover:bg-white/10 text-brand-grey hover:text-white transition-colors"
                            >
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        {/* Step Indicator */}
                        <div className="px-6 pt-4 flex items-center gap-2">
                            {[1, 2, 3].map((s) => (
                                <React.Fragment key={s}>
                                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold transition-all ${modalStep >= s
                                        ? 'bg-brand-accent text-white'
                                        : 'bg-white/5 text-brand-grey'
                                        }`}>
                                        {s}
                                    </div>
                                    {s < 3 && (
                                        <div className={`flex-1 h-0.5 rounded ${modalStep > s ? 'bg-brand-accent' : 'bg-white/10'
                                            }`} />
                                    )}
                                </React.Fragment>
                            ))}
                        </div>

                        {/* Step Content */}
                        <div className="p-6">
                            {/* ── STEP 1: Type Selection ── */}
                            {modalStep === 1 && (
                                <div className="space-y-3">
                                    <p className="text-sm text-brand-grey mb-4">Select the type of model you want to add:</p>
                                    <button
                                        onClick={() => { setModelType('open_source'); setFormProvider('ollama'); setModalStep(2) }}
                                        className="w-full flex items-center gap-4 p-4 rounded-xl border border-white/5 bg-white/[0.02] hover:bg-brand-accent/10 hover:border-brand-accent/30 transition-all group cursor-pointer"
                                    >
                                        <div className="w-10 h-10 rounded-lg bg-green-500/10 flex items-center justify-center">
                                            <Box className="w-5 h-5 text-green-400" />
                                        </div>
                                        <div className="text-left flex-1">
                                            <div className="text-sm font-semibold text-white">Open Source</div>
                                            <div className="text-xs text-brand-grey">Ollama models (Llama, Qwen, Mistral, etc.)</div>
                                        </div>
                                        <ChevronRight className="w-4 h-4 text-brand-grey group-hover:text-brand-accent transition-colors" />
                                    </button>

                                    <button
                                        onClick={() => { setModelType('paid'); setFormProvider('openai'); setModalStep(2) }}
                                        className="w-full flex items-center gap-4 p-4 rounded-xl border border-white/5 bg-white/[0.02] hover:bg-brand-accent/10 hover:border-brand-accent/30 transition-all group cursor-pointer"
                                    >
                                        <div className="w-10 h-10 rounded-lg bg-purple-500/10 flex items-center justify-center">
                                            <Radio className="w-5 h-5 text-purple-400" />
                                        </div>
                                        <div className="text-left flex-1">
                                            <div className="text-sm font-semibold text-white">Paid / API</div>
                                            <div className="text-xs text-brand-grey">OpenAI, Anthropic, Google Gemini</div>
                                        </div>
                                        <ChevronRight className="w-4 h-4 text-brand-grey group-hover:text-brand-accent transition-colors" />
                                    </button>
                                </div>
                            )}

                            {/* ── STEP 2: Configuration ── */}
                            {modalStep === 2 && modelType === 'open_source' && (
                                <div className="space-y-4">
                                    <p className="text-sm text-brand-grey mb-2">
                                        {isEditing ? `Editing ${formModel}:` : 'Configure your open-source model:'}
                                    </p>
                                    <div>
                                        <label className="block text-xs text-brand-grey uppercase tracking-wider mb-1.5 font-bold">Model Name</label>
                                        <input
                                            value={formModel}
                                            onChange={(e) => setFormModel(e.target.value)}
                                            placeholder="e.g. llama3.2:latest"
                                            className="w-full px-4 py-2.5 rounded-xl bg-white/5 border border-white/10 text-white text-sm focus:border-brand-accent focus:ring-1 focus:ring-brand-accent outline-none transition-all placeholder:text-brand-grey/50"
                                        />
                                    </div>
                                    <div>
                                        <label className="block text-xs text-brand-grey uppercase tracking-wider mb-1.5 font-bold">Ollama Base URL</label>
                                        <input
                                            value={formBaseUrl}
                                            onChange={(e) => setFormBaseUrl(e.target.value)}
                                            placeholder="http://localhost:11434"
                                            className="w-full px-4 py-2.5 rounded-xl bg-white/5 border border-white/10 text-white text-sm focus:border-brand-accent focus:ring-1 focus:ring-brand-accent outline-none transition-all placeholder:text-brand-grey/50"
                                        />
                                    </div>
                                    <div className="flex gap-3 pt-2">
                                        {!isEditing && (
                                            <button
                                                onClick={() => setModalStep(1)}
                                                className="px-4 py-2 rounded-xl bg-white/5 text-brand-grey text-sm hover:bg-white/10 transition-colors cursor-pointer"
                                            >
                                                Back
                                            </button>
                                        )}
                                        <button
                                            onClick={handleAddModel}
                                            disabled={!formModel.trim()}
                                            className="flex-1 px-4 py-2.5 rounded-xl bg-brand-accent text-white text-sm font-medium hover:bg-brand-accent/80 transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                                        >
                                            Verify & Activate
                                        </button>
                                    </div>
                                </div>
                            )}

                            {modalStep === 2 && modelType === 'paid' && (
                                <div className="space-y-4">
                                    <p className="text-sm text-brand-grey mb-2">
                                        {isEditing ? `Editing ${formModel}:` : 'Configure your paid model:'}
                                    </p>
                                    <div>
                                        <label className="block text-xs text-brand-grey uppercase tracking-wider mb-1.5 font-bold">Provider</label>
                                        <select
                                            value={formProvider}
                                            onChange={(e) => setFormProvider(e.target.value)}
                                            className="w-full px-4 py-2.5 rounded-xl bg-white/5 border border-white/10 text-white text-sm focus:border-brand-accent outline-none transition-all"
                                        >
                                            <option value="openai">OpenAI (ChatGPT)</option>
                                            <option value="anthropic">Anthropic (Claude)</option>
                                            <option value="gemini">Google Gemini</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label className="block text-xs text-brand-grey uppercase tracking-wider mb-1.5 font-bold">Model Name</label>
                                        <input
                                            value={formModel}
                                            onChange={(e) => setFormModel(e.target.value)}
                                            placeholder={formProvider === 'openai' ? 'gpt-4o-mini' : formProvider === 'anthropic' ? 'claude-3-haiku-20240307' : 'gemini-1.5-flash'}
                                            className="w-full px-4 py-2.5 rounded-xl bg-white/5 border border-white/10 text-white text-sm focus:border-brand-accent focus:ring-1 focus:ring-brand-accent outline-none transition-all placeholder:text-brand-grey/50"
                                        />
                                    </div>
                                    <div>
                                        <label className="block text-xs text-brand-grey uppercase tracking-wider mb-1.5 font-bold">API Key</label>
                                        <input
                                            type="password"
                                            value={formApiKey}
                                            onChange={(e) => setFormApiKey(e.target.value)}
                                            placeholder="sk-..."
                                            className="w-full px-4 py-2.5 rounded-xl bg-white/5 border border-white/10 text-white text-sm focus:border-brand-accent focus:ring-1 focus:ring-brand-accent outline-none transition-all placeholder:text-brand-grey/50"
                                        />
                                    </div>
                                    <div className="flex gap-3 pt-2">
                                        {!isEditing && (
                                            <button
                                                onClick={() => setModalStep(1)}
                                                className="px-4 py-2 rounded-xl bg-white/5 text-brand-grey text-sm hover:bg-white/10 transition-colors cursor-pointer"
                                            >
                                                Back
                                            </button>
                                        )}
                                        <button
                                            onClick={handleAddModel}
                                            disabled={!formModel.trim() || !formApiKey.trim()}
                                            className="flex-1 px-4 py-2.5 rounded-xl bg-brand-accent text-white text-sm font-medium hover:bg-brand-accent/80 transition-all disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                                        >
                                            Verify & Activate
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* ── STEP 3: Verification ── */}
                            {modalStep === 3 && (
                                <div className="space-y-4">
                                    {verifying && (
                                        <div className="flex flex-col items-center py-8 gap-4">
                                            <Loader2 className="w-10 h-10 text-brand-accent animate-spin" />
                                            <p className="text-sm text-brand-grey">Verifying model integration...</p>
                                            <p className="text-xs text-brand-grey/60">Testing connectivity, streaming & tool calling</p>
                                        </div>
                                    )}

                                    {!verifying && verifyResult && (
                                        <>
                                            {/* Overall Result */}
                                            <div className={`flex items-center gap-3 p-4 rounded-xl border ${verifyResult.success
                                                ? 'bg-green-500/10 border-green-500/20'
                                                : 'bg-red-500/10 border-red-500/20'
                                                }`}>
                                                {verifyResult.success
                                                    ? <CheckCircle2 className="w-6 h-6 text-green-400" />
                                                    : <XCircle className="w-6 h-6 text-red-400" />
                                                }
                                                <div>
                                                    <div className={`text-sm font-semibold ${verifyResult.success ? 'text-green-300' : 'text-red-300'}`}>
                                                        {verifyResult.success ? 'Verification Passed!' : 'Verification Failed'}
                                                    </div>
                                                    <div className="text-xs text-brand-grey mt-0.5">{verifyResult.message}</div>
                                                </div>
                                            </div>

                                            {/* Phase Results */}
                                            <div className="space-y-2">
                                                {[
                                                    { label: 'Connectivity', ok: verifyResult.connectivity_ok },
                                                    { label: 'Streaming', ok: verifyResult.streaming_ok },
                                                    { label: 'Tool Calling', ok: verifyResult.tools_ok },
                                                ].map((phase) => (
                                                    <div key={phase.label} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/[0.02]">
                                                        {phase.ok
                                                            ? <CheckCircle2 className="w-4 h-4 text-green-400" />
                                                            : <XCircle className="w-4 h-4 text-red-400" />
                                                        }
                                                        <span className="text-sm text-gray-300">{phase.label}</span>
                                                    </div>
                                                ))}
                                            </div>

                                            {/* Details Log */}
                                            {verifyResult.details.length > 0 && (
                                                <div className="p-3 rounded-xl bg-black/30 max-h-40 overflow-y-auto">
                                                    {verifyResult.details.map((d, i) => (
                                                        <div key={i} className="text-xs text-brand-grey font-mono py-0.5">
                                                            {d}
                                                        </div>
                                                    ))}
                                                </div>
                                            )}

                                            <div className="pt-2">
                                                <button
                                                    onClick={resetModal}
                                                    className="w-full px-4 py-2.5 rounded-xl bg-white/5 text-white text-sm hover:bg-white/10 transition-colors cursor-pointer"
                                                >
                                                    {verifyResult.success ? 'Done' : 'Close'}
                                                </button>
                                            </div>
                                        </>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
