import React from 'react';
import { LayoutGrid, MessageSquare, Briefcase, Settings, Plus, Trash2, Box } from 'lucide-react';

interface Conversation {
    id: string;
    title: string;
    updated_at: string;
}

interface SidebarProps {
    conversations: Conversation[];
    currentId?: string;
    onSelect: (id: string) => void;
    onNew: () => void;
    onDelete: (id: string, e: React.MouseEvent) => void;
    onPluginsClick: () => void;
    onChatsClick: () => void;
    activeTab: 'chats' | 'plugins';
}

export const Sidebar: React.FC<SidebarProps> = ({
    conversations,
    currentId,
    onSelect,
    onNew,
    onDelete,
    onPluginsClick,
    onChatsClick,
    activeTab
}) => {
    return (
        <div className="w-64 flex-shrink-0 flex flex-col h-full bg-brand-surface border-r border-white/5 text-white font-sans p-4">
            {/* Header */}
            <div className="mb-8 px-2 flex items-center gap-2">
                <div className="w-8 h-8 rounded bg-white text-black font-bold flex items-center justify-center text-xl tracking-tight">
                    V
                </div>
                <h1 className="font-bold text-xl tracking-wide">
                    VOXA
                </h1>
            </div>

            {/* Navigation (Static for Style) */}
            <div className="space-y-1 mb-6">
                <div className="flex items-center gap-3 px-3 py-2 text-brand-grey hover:text-white transition-colors cursor-pointer rounded hover:bg-white/5">
                    <Briefcase className="w-4 h-4" />
                    <span className="text-sm font-medium">My projects</span>
                </div>
                <div
                    onClick={onChatsClick}
                    className={`flex items-center gap-3 px-3 py-2 cursor-pointer rounded transition-colors ${activeTab === 'chats' ? 'text-brand-accent bg-brand-accent/10' : 'text-brand-grey hover:text-white hover:bg-white/5'}`}
                >
                    <MessageSquare className="w-4 h-4" />
                    <span className="text-sm font-medium">Chats</span>
                </div>
                <div
                    onClick={onPluginsClick}
                    className={`flex items-center gap-3 px-3 py-2 cursor-pointer rounded transition-colors ${activeTab === 'plugins' ? 'text-brand-accent bg-brand-accent/10' : 'text-brand-grey hover:text-white hover:bg-white/5'}`}
                >
                    <Box className="w-4 h-4" />
                    <span className="text-sm font-medium">Plugins</span>
                </div>
                <div className="flex items-center gap-3 px-3 py-2 text-brand-grey hover:text-white transition-colors cursor-pointer rounded hover:bg-white/5">
                    <LayoutGrid className="w-4 h-4" />
                    <span className="text-sm font-medium">Templates</span>
                </div>
                <div className="flex items-center gap-3 px-3 py-2 text-brand-grey hover:text-white transition-colors cursor-pointer rounded hover:bg-white/5">
                    <Settings className="w-4 h-4" />
                    <span className="text-sm font-medium">Settings</span>
                </div>
            </div>

            {/* Chats Header with Add Button */}
            <div className="flex items-center justify-between px-2 mb-2 text-brand-grey uppercase text-[10px] font-bold tracking-wider">
                <span>CHATS</span>
                <button onClick={onNew} className="hover:text-white transition-colors">
                    <Plus className="w-3 h-3" />
                </button>
            </div>

            {/* History List */}
            <div className="flex-1 overflow-y-auto min-h-0 container-snap -mx-2 px-2">
                <div className="space-y-0.5">
                    {conversations.map((conv) => (
                        <div
                            key={conv.id}
                            onClick={() => onSelect(conv.id)}
                            className={`
                                group flex items-center justify-between px-3 py-2 cursor-pointer transition-all rounded-lg
                                ${currentId === conv.id
                                    ? 'bg-white/10 text-white'
                                    : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'}
                            `}
                        >
                            <span className="truncate text-sm">
                                {conv.title || 'New Conversation'}
                            </span>
                            <button
                                onClick={(e) => onDelete(conv.id, e)}
                                className="opacity-0 group-hover:opacity-100 p-1 hover:text-red-500 transition-opacity"
                            >
                                <Trash2 className="w-3 h-3" />
                            </button>
                        </div>
                    ))}

                    {conversations.length === 0 && (
                        <div className="px-3 py-4 text-xs text-gray-600">
                            No active chats
                        </div>
                    )}
                </div>
            </div>

            {/* Observability (Hidden in Settings Conceptually, but explicit for verification) */}
            <div className="mt-auto pt-4 border-t border-white/5">
                <div className="text-[10px] text-brand-grey mb-2 px-2 uppercase font-bold">System Status</div>
                <div className="space-y-1">
                    <a href="http://localhost:3001" target="_blank" className="block px-3 py-1 text-xs text-brand-grey hover:text-brand-accent transition-colors">
                        Grafana
                    </a>
                    <a href="http://localhost:9090" target="_blank" className="block px-3 py-1 text-xs text-brand-grey hover:text-brand-accent transition-colors">
                        Prometheus
                    </a>
                </div>
            </div>

            {/* User Profile */}
            <div className="mt-4 pt-4 border-t border-white/5 flex items-center gap-3 px-2">
                <img
                    src="https://api.dicebear.com/7.x/avataaars/svg?seed=Felix"
                    alt="User"
                    className="w-8 h-8 rounded-full bg-brand-grey"
                />
                <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-white">Felix</div>
                    <div className="text-xs text-brand-grey">Pro Plan</div>
                </div>
            </div>
        </div>
    );
};
