import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { Users } from 'lucide-react';

const NOTICE_KEY = 'aava.v740.contexts-removal-notice-seen';

const LegacyContextsRedirect = () => {
    const navigate = useNavigate();
    const [seen] = useState(() => window.localStorage.getItem(NOTICE_KEY) === '1');

    if (seen) return <Navigate to="/agents" replace />;

    return (
        <div className="mx-auto max-w-2xl space-y-5 p-8">
            <div className="rounded-lg border border-border bg-card p-6 shadow-sm">
                <Users className="mb-4 h-9 w-9 text-primary" />
                <h1 className="text-2xl font-bold">Contexts were replaced by Agents</h1>
                <p className="mt-3 text-muted-foreground">
                    v7.4 uses Agents as the single configuration and routing model. Existing
                    Contexts are imported through the compatibility migration and can be reviewed
                    under Agents → Advanced.
                </p>
                <button
                    type="button"
                    onClick={() => {
                        window.localStorage.setItem(NOTICE_KEY, '1');
                        navigate('/agents', { replace: true });
                    }}
                    className="mt-5 inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90"
                >
                    Continue to Agents
                </button>
            </div>
        </div>
    );
};

export default LegacyContextsRedirect;
