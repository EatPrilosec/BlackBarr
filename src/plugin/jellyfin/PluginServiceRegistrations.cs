using System;
using Jellyfin.Plugin.BlackBarrHelper.Playback;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Plugins;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.BlackBarrHelper
{
    public class PluginServiceRegistrator : IPluginServiceRegistrator
    {
        public void RegisterServices(IServiceCollection serviceCollection, IServerApplicationHost applicationHost)
        {
            serviceCollection.AddSingleton<PlaybackInfoMiddleware>();
            serviceCollection.AddSingleton<IStartupFilter, PlaybackInfoStartupFilter>();
        }
    }

    public class PlaybackInfoStartupFilter : IStartupFilter
    {
        public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next)
        {
            return app =>
            {
                app.UseMiddleware<PlaybackInfoMiddleware>();
                next(app);
            };
        }
    }
}
