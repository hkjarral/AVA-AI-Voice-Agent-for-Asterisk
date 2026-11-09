import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';

export default function OnboardingBanner() {
    const [pending, setPending] = useState(false);
    useEffect(() => {
        let active = true;
        axios.get('/api/wizard/status').then(response => {
            if (active) setPending(response.data.stage === 'configuration_saved');
        }).catch(() => {});
        return () => { active = false; };
    }, []);
    return pending ? <div className="border rounded-lg p-4 bg-muted text-sm">Configuration saved; your first call is not yet verified. <Link className="underline font-medium" to="/wizard">Resume setup</Link></div> : null;
}
