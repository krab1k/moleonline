using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.ResponseCompression;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Mole.API.Models;

namespace Mole.API
{
    public class Startup
    {
        public Startup(IConfiguration configuration)
        {
            Configuration = configuration; // appsettings.json, appsettings.{env}.json and environment variables, see Program
        }

        public IConfiguration Configuration { get; }

        // This method gets called by the runtime. Use this method to add services to the container.
        public void ConfigureServices(IServiceCollection services)
        {
            services.Configure<Config>(Configuration.GetSection("Config")); // appsettings.json, environment variables Config__*
            services.AddSingleton<ComputationManager>();

            services.AddResponseCompression(x => {
                x.EnableForHttps = true;
                x.Providers.Add<GzipCompressionProvider>();
            }); //allow gzip compression

            services.AddCors(options =>
            {
                options.AddPolicy("CorsPolicy",
                    builder => builder.
                        AllowAnyOrigin().
                        AllowAnyMethod().
                        AllowAnyHeader().
                        WithExposedHeaders("Content-Disposition"));
            });// allow Cross-origin resource sharing (credentials cannot be combined with any origin)

            services.AddControllersWithViews().AddNewtonsoftJson();// Add framework services, keep Newtonsoft.Json for request bodies
        }

        // This method gets called by the runtime. Use this method to configure the HTTP request pipeline.
        public void Configure(IApplicationBuilder app)
        {
            app.UseExceptionHandler("/Index/Error");

            app.UseCors("CorsPolicy"); // before UseStaticFiles, the web fetches inputs/cofactors.json cross-origin
            app.UseStaticFiles(); // allow download files from wwwroot
            app.UseResponseCompression();
            app.UseRouting();
            app.UseEndpoints(endpoints =>
            {
                endpoints.MapControllers();
                endpoints.MapControllerRoute(
                    name: "Index",
                    pattern: "{controller=Index}/{action=Index}");
            });

            
        }
    }
}
