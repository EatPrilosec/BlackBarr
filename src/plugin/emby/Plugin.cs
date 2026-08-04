#nullable enable

using System;
using System.Collections.Generic;
using Emby.Plugin.BlackBarrHelper.Configuration;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;

namespace Emby.Plugin.BlackBarrHelper
{
    public class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
    {
        public override string Name => "BlackBarr Helper";

        public override Guid Id => Guid.Parse("e849b2c3-4d1a-429f-b78e-90f612d385a4");

        public override string Description => "Forces transcoding and injects dynamic black bar crop filters into Emby FFmpeg streams.";

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
                    Name = "blackbarrhelper",
                    DisplayName = "BlackBarr Helper",
                    EmbeddedResourcePath = GetType().Namespace + ".Configuration.configPage.html",
                    EnableInMainMenu = true,
                    MenuSection = "advanced",
                    MenuIcon = "tune",
                    IsMainConfigPage = true
                },
                new PluginPageInfo
                {
                    Name = "blackbarrhelperjs",
                    EmbeddedResourcePath = GetType().Namespace + ".Configuration.configPage.js",
                    EnableInMainMenu = false,
                    IsMainConfigPage = true
                }
            };
        }
    }
}
