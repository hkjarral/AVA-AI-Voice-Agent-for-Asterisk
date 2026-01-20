package troubleshoot

import "testing"

func TestDetectTransportFromConfigText(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		text string
		want string
	}{
		{
			name: "unquoted_audiosocket",
			text: "audio_transport: audiosocket\n",
			want: "audiosocket",
		},
		{
			name: "double_quoted_audiosocket",
			text: "audio_transport: \"audiosocket\"\n",
			want: "audiosocket",
		},
		{
			name: "single_quoted_externalmedia",
			text: "audio_transport: 'externalmedia'\n",
			want: "externalmedia",
		},
		{
			name: "trailing_comment",
			text: "audio_transport: externalmedia # default\n",
			want: "externalmedia",
		},
		{
			name: "indented_key",
			text: "  audio_transport: audiosocket\n",
			want: "audiosocket",
		},
		{
			name: "different_case",
			text: "audio_transport: ExternalMedia\n",
			want: "externalmedia",
		},
		{
			name: "missing_key",
			text: "other_key: audiosocket\n",
			want: "",
		},
		{
			name: "empty",
			text: "",
			want: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := detectTransportFromConfigText(tt.text); got != tt.want {
				t.Fatalf("detectTransportFromConfigText() = %q, want %q", got, tt.want)
			}
		})
	}
}

