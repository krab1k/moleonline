using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.AspNetCore.ResponseCompression;
using Mole.API;
using Mole.API.Models;

var builder = WebApplication.CreateBuilder(args); // Kestrel, appsettings.json, appsettings.{env}.json, environment variables, logging

builder.Services.Configure<Config>(builder.Configuration.GetSection("Config")); // appsettings.json, environment variables Config__*
builder.Services.AddSingleton<ComputationManager>();

builder.Services.AddResponseCompression(x =>
{
    x.EnableForHttps = true;
    x.Providers.Add<GzipCompressionProvider>();
}); // allow gzip compression

builder.Services.AddCors(options =>
{
    options.AddPolicy("CorsPolicy",
        policy => policy.
            AllowAnyOrigin().
            AllowAnyMethod().
            AllowAnyHeader().
            WithExposedHeaders("Content-Disposition"));
}); // allow Cross-origin resource sharing (credentials cannot be combined with any origin)

builder.Services.Configure<ForwardedHeadersOptions>(options =>
{
    options.ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto;
    // nginx runs in another container with an address of the Docker network, not known in advance;
    // public addresses cannot pass a client address, only loopback and private networks
    options.KnownIPNetworks.Add(System.Net.IPNetwork.Parse("10.0.0.0/8"));
    options.KnownIPNetworks.Add(System.Net.IPNetwork.Parse("172.16.0.0/12"));
    options.KnownIPNetworks.Add(System.Net.IPNetwork.Parse("192.168.0.0/16"));
    options.KnownIPNetworks.Add(System.Net.IPNetwork.Parse("fc00::/7"));
}); // the client address from the reverse proxy (nginx)

builder.Services.AddControllersWithViews().AddNewtonsoftJson(); // keep Newtonsoft.Json for request bodies

var app = builder.Build();

app.UseForwardedHeaders(); // first, everything after it sees the client address
app.UseExceptionHandler("/Index/Error");
app.UseCors("CorsPolicy"); // before UseStaticFiles, the web fetches inputs/cofactors.json cross-origin
app.UseStaticFiles(); // allow download files from wwwroot
app.UseResponseCompression();
app.UseRouting();

app.MapControllers();
app.MapControllerRoute(
    name: "Index",
    pattern: "{controller=Index}/{action=Index}");

app.Run();
