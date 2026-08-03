using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.BlackBarrHelper.Configuration
{
    public class PluginConfiguration : BasePluginConfiguration
    {
        public string BlackBarrUrl { get; set; } = "http://blackbarr:6795";
        public bool ForceTranscodeAll { get; set; } = false;
        public bool ForceTranscodeCropped { get; set; } = true;
    }
}
