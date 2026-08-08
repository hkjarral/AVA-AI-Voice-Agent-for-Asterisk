import React from 'react';
import { NavLink } from 'react-router-dom';
import {
    LayoutDashboard,
    Server,
    Workflow,
    Users,
    Wrench,
    Plug,
    Sliders,
    Activity,
    Zap,
    Brain,
    Radio,
    Globe,
    Container,
    FileText,
    Terminal,
    AlertTriangle,
    Code,
    HelpCircle,
    ExternalLink,
    Coffee,
    Heart,
    HardDrive,
    ArrowUpCircle,
    Phone,
    CalendarClock,
    LogOut,
    Lock,
    ChevronsLeft,
    ChevronsRight
} from 'lucide-react';
import { useAuth } from '../../auth/AuthContext';
import { KOFI_URL, SPONSORS_URL } from '../../config/donation';
import { useSidebarCollapsed } from '../../hooks/useSidebarCollapsed';
import ChangePasswordModal from '../auth/ChangePasswordModal';
import { useState } from 'react';

// Shares the collapsed state with the nested item/group components without
// threading a prop through every SidebarItem.
const CollapseContext = React.createContext(false);

const itemClasses = (collapsed: boolean, active: boolean) =>
    `flex items-center ${collapsed ? 'justify-center' : 'gap-3'} px-3 py-2 rounded-md text-sm font-medium transition-colors ${active
        ? 'bg-primary/10 text-primary'
        : 'text-muted-foreground hover:bg-accent hover:text-foreground'
    }`;

const SidebarItem = ({ to, icon: Icon, label, end = false }: { to: string, icon: any, label: string, end?: boolean }) => {
    const collapsed = React.useContext(CollapseContext);
    return (
        <NavLink
            to={to}
            end={end}
            title={collapsed ? label : undefined}
            aria-label={collapsed ? label : undefined}
            className={({ isActive }) => itemClasses(collapsed, isActive)}
        >
            <Icon className="w-4 h-4 shrink-0" />
            {!collapsed && <span>{label}</span>}
        </NavLink>
    );
};

const ExternalItem = ({ href, icon: Icon, label, ariaLabel }: { href: string, icon: any, label: string, ariaLabel?: string }) => {
    const collapsed = React.useContext(CollapseContext);
    return (
        <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={ariaLabel ?? (collapsed ? label : undefined)}
            title={collapsed ? label : undefined}
            className={itemClasses(collapsed, false)}
        >
            <Icon className="w-4 h-4 shrink-0" aria-hidden="true" />
            {!collapsed && <span>{label}</span>}
        </a>
    );
};

const SidebarGroup = ({ title, children }: { title: string, children: React.ReactNode }) => {
    const collapsed = React.useContext(CollapseContext);
    return (
        <div className="mb-6">
            {!collapsed && (
                <h3 className="px-3 mb-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    {title}
                </h3>
            )}
            <div className="space-y-1">
                {children}
            </div>
        </div>
    );
};

const CollapseToggle = ({ collapsed, onToggle }: { collapsed: boolean, onToggle: () => void }) => {
    const label = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    return (
        <button
            type="button"
            onClick={onToggle}
            aria-label={label}
            aria-expanded={!collapsed}
            title={label}
            className="p-2 rounded-md text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
        >
            {collapsed ? <ChevronsRight className="w-4 h-4" /> : <ChevronsLeft className="w-4 h-4" />}
        </button>
    );
};

const Sidebar = () => {
    const { user, logout } = useAuth();
    const [isPasswordModalOpen, setIsPasswordModalOpen] = useState(false);
    const { collapsed, toggle } = useSidebarCollapsed();

    return (
        <CollapseContext.Provider value={collapsed}>
            <aside className={`${collapsed ? 'w-16' : 'w-64'} border-r border-border bg-card/50 backdrop-blur flex flex-col h-full transition-[width] duration-200`}>
                <div className={`${collapsed ? 'p-3' : 'p-6'} border-b border-border/50`}>
                    <div className={`flex items-center ${collapsed ? 'justify-center' : 'gap-3'} font-bold text-xl tracking-tight`}>
                        <img
                            src="/mascot_transparent.png"
                            alt="AVA Mascot"
                            className="w-11 h-11 object-contain shrink-0"
                        />
                        {!collapsed && (
                            <div className="flex flex-col leading-none flex-1 min-w-0">
                                <span>AVA</span>
                                <span className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider mt-1">AI Voice Agent for Asterisk</span>
                            </div>
                        )}
                        {!collapsed && <CollapseToggle collapsed={collapsed} onToggle={toggle} />}
                    </div>
                    {collapsed && (
                        <div className="flex justify-center mt-3">
                            <CollapseToggle collapsed={collapsed} onToggle={toggle} />
                        </div>
                    )}
                </div>

                <nav aria-label="Main navigation" className="flex-1 overflow-y-auto py-6 px-3">
                    <SidebarGroup title="Overview">
                        <SidebarItem to="/" icon={LayoutDashboard} label="Dashboard" end />
                        <SidebarItem to="/history" icon={Phone} label="Call History" />
                        <SidebarItem to="/scheduling" icon={CalendarClock} label="Call Scheduling" />
                        <SidebarItem to="/wizard" icon={Zap} label="Setup Wizard" />
                    </SidebarGroup>

                    <SidebarGroup title="Core Configuration">
                        <SidebarItem to="/agents" icon={Users} label="Agents" />
                        <SidebarItem to="/providers" icon={Server} label="Providers" />
                        <SidebarItem to="/pipelines" icon={Workflow} label="Pipelines" />
                        <SidebarItem to="/profiles" icon={Sliders} label="Audio Profiles" />
                        <SidebarItem to="/tools" icon={Wrench} label="Tools" />
                        <SidebarItem to="/mcp" icon={Plug} label="MCP" />
                    </SidebarGroup>

                    <SidebarGroup title="Advanced Settings">
                        <SidebarItem to="/vad" icon={Activity} label="Voice Activity Detection" />
                        <SidebarItem to="/streaming" icon={Zap} label="Streaming" />
                        <SidebarItem to="/llm" icon={Brain} label="LLM Defaults" />
                        <SidebarItem to="/transport" icon={Radio} label="Audio Transport" />
                        <SidebarItem to="/barge-in" icon={AlertTriangle} label="Barge-in" />
                    </SidebarGroup>

                    <SidebarGroup title="System">
                        <SidebarItem to="/env" icon={Globe} label="Environment" />
                        <SidebarItem to="/docker" icon={Container} label="Docker Services" />
                        <SidebarItem to="/asterisk" icon={Phone} label="Asterisk" />
                        <SidebarItem to="/models" icon={HardDrive} label="Models" />
                        <SidebarItem to="/updates" icon={ArrowUpCircle} label="Updates" />
                        <SidebarItem to="/logs" icon={FileText} label="Logs" />
                        <SidebarItem to="/terminal" icon={Terminal} label="Terminal" />
                    </SidebarGroup>

                    <SidebarGroup title="Danger Zone">
                        <SidebarItem to="/yaml" icon={Code} label="Raw YAML" />
                    </SidebarGroup>

                    <SidebarGroup title="Support">
                        <SidebarItem to="/help" icon={HelpCircle} label="Help" />
                        <ExternalItem href="/docs" icon={ExternalLink} label="API Docs" />
                        <ExternalItem href={KOFI_URL} icon={Coffee} label="Support on Ko-fi" ariaLabel="Support AVA on Ko-fi" />
                        <ExternalItem href={SPONSORS_URL} icon={Heart} label="Sponsor" ariaLabel="Sponsor AVA on GitHub" />
                    </SidebarGroup>
                </nav>

                <div className="p-4 border-t border-border/50">
                    <div className={`flex items-center mb-3 ${collapsed ? 'justify-center' : 'gap-3 px-2'}`}>
                        <div className="w-8 h-8 rounded-full bg-accent flex items-center justify-center text-xs font-bold uppercase shrink-0">
                            {user?.username?.substring(0, 2) || 'AD'}
                        </div>
                        {!collapsed && (
                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium truncate">{user?.username || 'Admin'}</p>
                                <p className="text-xs text-muted-foreground truncate">Administrator</p>
                            </div>
                        )}
                    </div>
                    <div className={`flex gap-2 ${collapsed ? 'flex-col items-center' : ''}`}>
                        <button
                            onClick={() => setIsPasswordModalOpen(true)}
                            className={`${collapsed ? '' : 'flex-1'} flex items-center justify-center gap-2 px-2 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-accent rounded-md transition-colors`}
                            title="Change Password"
                            aria-label={collapsed ? 'Change Password' : undefined}
                        >
                            <Lock className="w-3 h-3" />
                            {!collapsed && 'Password'}
                        </button>
                        <button
                            onClick={logout}
                            className={`${collapsed ? '' : 'flex-1'} flex items-center justify-center gap-2 px-2 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 rounded-md transition-colors`}
                            title="Logout"
                            aria-label={collapsed ? 'Logout' : undefined}
                        >
                            <LogOut className="w-3 h-3" />
                            {!collapsed && 'Logout'}
                        </button>
                    </div>
                </div>

                <ChangePasswordModal
                    isOpen={isPasswordModalOpen}
                    onClose={() => setIsPasswordModalOpen(false)}
                />
            </aside>
        </CollapseContext.Provider>
    );
};

export default Sidebar;
