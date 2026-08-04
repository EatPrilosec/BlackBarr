#nullable enable

using MediaBrowser.Model.Plugins;

namespace Emby.Plugin.BlackBarrHelper.Configuration
{
    public class PluginConfiguration : BasePluginConfiguration
    {
        public string BlackBarrUrl { get; set; } = "http://127.0.0.1:6795";

        public bool ForceTranscodeCropped { get; set; } = true;

        public bool ForceTranscodeAll { get; set; } = false;
    }
}
