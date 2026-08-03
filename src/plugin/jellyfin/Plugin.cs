using System;
using System.Collections.Generic;
using Jellyfin.Plugin.BlackBarrHelper.Configuration;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;

namespace Jellyfin.Plugin.BlackBarrHelper
{
    public class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
    {
        public override string Name => "BlackBarr Helper";

        public override Guid Id => Guid.Parse("b598b9e4-6a2c-476b-9c71-2191419736c4");

        public override string Description => "Forces transcoding and injects dynamic black bar crop filters into Jellyfin FFmpeg streams.";

        public static Plugin? Instance { get; private set; }

        public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
            : base(applicationPaths, xmlSerializer)
        {
            Instance = this;
        }

        public IEnumerable<PluginPageInfo> GetPages()
        {
            return new[]
            {
                new PluginPageInfo
                {
                    Name = Name,
                    EmbeddedResourcePath = GetType().Namespace + ".Configuration.configPage.html"
                }
            };
        }
    }
}
