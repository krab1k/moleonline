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

builder.Services.AddControllersWithViews().AddNewtonsoftJson(); // keep Newtonsoft.Json for request bodies

var app = builder.Build();

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
