import React from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import Header from './Header';

const AppShell = () => {
    return (
        <div className="flex h-screen bg-background text-foreground font-sans overflow-hidden">
            <Sidebar />

            <main className="flex-1 flex flex-col min-w-0">
                <Header />

                <div className="flex-1 overflow-auto p-6">
                    <div className="max-w-6xl mx-auto">
                        <Outlet />
                    </div>
                </div>
            </main>
        </div>
    );
};

export default AppShell;
