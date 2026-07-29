import React, { useState } from 'react';
import { X, Lock } from 'lucide-react';
import { useAuth } from '../../auth/AuthContext';
import { FormInput } from '../ui/FormComponents';

interface ChangePasswordModalProps {
    isOpen: boolean;
    onClose: () => void;
    mandatory?: boolean;  // If true, user cannot close modal without changing password
}

const ChangePasswordModal: React.FC<ChangePasswordModalProps> = ({ isOpen, onClose, mandatory = false }) => {
    const [oldPassword, setOldPassword] = useState('');
    const [newPassword, setNewPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [error, setError] = useState('');
    const [success, setSuccess] = useState('');
    const [loading, setLoading] = useState(false);
    const { changePassword, logout } = useAuth();

    if (!isOpen) return null;

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setSuccess('');

        if (newPassword !== confirmPassword) {
            setError('New passwords do not match');
            return;
        }

        if (newPassword.length < 5) {
            setError('Password must be at least 5 characters');
            return;
        }

        setLoading(true);
        try {
            await changePassword(oldPassword, newPassword);
            setSuccess('Password updated successfully');
            setOldPassword('');
            setNewPassword('');
            setConfirmPassword('');
            setTimeout(() => {
                onClose();
                setSuccess('');
            }, 1500);
        } catch (err: any) {
            setError(err.response?.data?.detail || 'Failed to update password');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <div className="bg-card w-full max-w-md rounded-lg border border-border shadow-lg p-6 relative">
                {!mandatory && (
                    <button
                        onClick={onClose}
                        aria-label="Close"
                        className="absolute top-4 right-4 text-muted-foreground hover:text-foreground"
                    >
                        <X className="w-5 h-5" />
                    </button>
                )}

                <div className="flex items-center gap-2 mb-6">
                    <div className="p-2 bg-primary/10 rounded-full text-primary">
                        <Lock className="w-5 h-5" />
                    </div>
                    <h2 className="text-xl font-semibold">
                        {mandatory ? 'Password Change Required' : 'Change Password'}
                    </h2>
                </div>

                {mandatory && (
                    <div className="mb-4 p-3 bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 rounded text-sm">
                        For security, you must change your password before continuing. If you mistyped your one-time password, use Logout to sign in again.
                    </div>
                )}

                {error && (
                    <div className="mb-4 p-3 bg-destructive/10 border border-destructive/20 text-destructive rounded text-sm">
                        {error}
                    </div>
                )}

                {success && (
                    <div className="mb-4 p-3 bg-green-500/10 border border-green-500/20 text-green-600 dark:text-green-400 rounded text-sm">
                        {success}
                    </div>
                )}

                <form onSubmit={handleSubmit} className="space-y-4">
                    <FormInput
                        label="Current Password"
                        type="password"
                        value={oldPassword}
                        onChange={(e) => setOldPassword(e.target.value)}
                        required
                    />
                    <FormInput
                        label="New Password"
                        type="password"
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        required
                    />
                    <FormInput
                        label="Confirm New Password"
                        type="password"
                        value={confirmPassword}
                        onChange={(e) => setConfirmPassword(e.target.value)}
                        required
                    />

                    <div className="flex justify-end gap-3 mt-6">
                        {mandatory ? (
                            <button
                                type="button"
                                onClick={logout}
                                className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground"
                            >
                                Logout
                            </button>
                        ) : (
                            <button
                                type="button"
                                onClick={onClose}
                                className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground"
                            >
                                Cancel
                            </button>
                        )}
                        <button
                            type="submit"
                            disabled={loading}
                            className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
                        >
                            {loading ? 'Updating...' : 'Update Password'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
};

export default ChangePasswordModal;
